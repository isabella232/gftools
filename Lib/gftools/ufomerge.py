from fontFeatures import FontFeatures, Routine, Substitution
from fontFeatures.feaLib import FeaParser
from pathlib import Path
import logging

logger = logging.getLogger("ufomerge")
logging.basicConfig(level=logging.INFO)


def merge_ufos(
    ufo1,
    ufo2,
    glyphs=None,
    exclude_glyphs=None,
    codepoints=None,
    layout_handling="subset",
    existing_handling="replace",
):
    if glyphs is None:
        glyphs = []
    if exclude_glyphs:
        glyphs = set(glyphs) - set(exclude_glyphs)

    glyphs = set(glyphs)

    if codepoints:
        cp2glyph = {}
        for g in ufo2:
            for u in g.unicodes:
                cp2glyph[u] = g.name
        glyphs |= set(cp2glyph[c] for c in codepoints if c in cp2glyph)

    # Check those glyphs actually are in UFO 2
    not_there = glyphs - set(ufo2.keys())
    if len(not_there):
        logger.warn("The following glyphs were not in UFO 2: %s" % ", ".join(not_there))
        glyphs = glyphs - not_there

    if not glyphs:
        logger.info("No glyphs selected, nothing to do")
        exit(0)

    newglyphset = set(ufo1.keys()) | set(glyphs)

    # Handle layout subsetting here, in case closure is needed
    new_layout_rules = FontFeatures()
    if layout_handling == "ignore":
        pass
    else:
        path = getattr(ufo2, "_path", None)
        includeDir = Path(ufo2._path).parent if path else None
        ff = FeaParser(ufo2.features.text, includeDir=includeDir).parse()
        for routine in ff.routines:
            newroutine = Routine(name=routine.name, flags=routine.flags)
            for rule in routine.rules:
                if not isinstance(rule, Substitution):
                    continue
                flat_outputs = [
                    item for sublist in rule.replacement for item in sublist
                ]
                rule.input = [list(set(r) & newglyphset) for r in rule.input]
                rule.precontext = [list(set(r) & newglyphset) for r in rule.precontext]
                rule.postcontext = [
                    list(set(r) & newglyphset) for r in rule.postcontext
                ]
                if (
                    any(not g for g in rule.input)
                    or any(not g for g in rule.precontext)
                    or any(not g for g in rule.postcontext)
                ):
                    continue
                if layout_handling == "closure":
                    # Any glyphs from "glyphs" substituted or generated by rules need to be added to the glyph set
                    if not any(g in glyphs for g in rule.involved_glyphs):
                        continue
                    glyphs |= set(flat_outputs)
                else:
                    # Any rules with new glyphs on the right hand side and glyphs
                    # we have on the left hand side need to be copied into UFO1
                    if not any(g in glyphs for g in flat_outputs):
                        continue
                    logging.debug("Adding rule '%s'", rule.asFea())
                newroutine.rules.append(rule)
            if newroutine.rules:
                # Was it in a feature?
                add_to = []
                for feature_name, routines in ff.features.items():
                    for routine_ref in routines:
                        if routine_ref.routine == routine:
                            add_to.append(feature_name)
                for feature_name in add_to:
                    new_layout_rules.addFeature(feature_name, [newroutine])

    # Kerning!!
    # # Create a list of flat kerning pairs for UFO 1
    # ufo1_kerns = set()
    # for l,r in ufo1.kerning.keys():
    #     l = ufo1.groups.get(l,[l])
    #     r = ufo1.groups.get(r,[r])
    #     for lg in l:
    #         for rg in r:
    #             ufo1_kerns.add((lg,rg))

    # Slim down the groups to only those in the glyph set
    for g in ufo2.groups.keys():
        ufo2.groups[g] = [g for g in ufo2.groups[g] if g in newglyphset]

    for (l, r), value in ufo2.kerning.items():
        lg = ufo2.groups.get(l, [l])
        rg = ufo2.groups.get(r, [r])
        if not lg or not rg:
            continue
        if any(lglyph not in newglyphset for lglyph in lg) or any(
            rglyph not in newglyphset for rglyph in rg
        ):
            continue
        # Just add for now. We should get fancy later
        ufo1.kerning[(l, r)] = value
        if l.startswith("public.kern"):
            if l not in ufo1.groups:
                ufo1.groups[l] = ufo2.groups[l]
            else:
                ufo1.groups[l] = list(set(ufo1.groups[l] + ufo2.groups[l]))
        if r.startswith("public.kern"):
            if r not in ufo1.groups:
                ufo1.groups[r] = ufo2.groups[r]
            else:
                ufo1.groups[r] = list(set(ufo1.groups[r] + ufo2.groups[r]))

    # Routines for merging font lib keys
    def merge_set(ufo1, ufo2, name, g, create_if_not_in_ufo1=False):
        if name not in ufo2.lib or g not in ufo2.lib[name]:
            return
        if name not in ufo1.lib:
            if create_if_not_in_ufo1:
                ufo1.lib[name] = []
            else:
                return
        if g not in ufo1.lib[name]:
            ufo1.lib[name].append(g)

    def merge_dict(ufo1, ufo2, name, g, create_if_not_in_ufo1=False):
        if name not in ufo2.lib or g not in ufo2.lib[name]:
            return
        if name not in ufo1.lib:
            if create_if_not_in_ufo1:
                ufo1.lib[name] = {}
            else:
                return
        ufo1.lib[name][g] = ufo2.lib[name][g]

    # Check the glyphs for components
    def close_components(glyphs, g):
        if not ufo2[g].components:
            return
        for comp in ufo2[g].components:
            if comp.baseGlyph not in newglyphset:
                # Well, this is the easy case
                glyphs.add(comp.baseGlyph)
                close_components(glyphs, comp.baseGlyph)
            elif existing_handling == "replace":
                # Also not a problem
                glyphs.add(comp.baseGlyph)
                close_components(glyphs, comp.baseGlyph)
            elif comp.baseGlyph in ufo1:
                # Oh bother.
                logger.warning(
                    f"New glyph {g} used component {comp.baseGlyph} which already exists in font; not replacing it, as you have not specified --replace-existing"
                )

    for g in list(glyphs):  # list() avoids "Set changed size during iteration" error
        close_components(glyphs, g)

    # Now do the add
    for g in glyphs:
        if existing_handling == "skip" and g in ufo1:
            logger.info("Skipping glyph '%s' already present in target file" % g)
            continue

        merge_set(ufo1, ufo2, "public.glyphOrder", g, create_if_not_in_ufo1=False)
        merge_set(ufo1, ufo2, "public.skipExportGlyphs", g, create_if_not_in_ufo1=True)
        merge_dict(ufo1, ufo2, "public.postscriptNames", g, create_if_not_in_ufo1=True)
        merge_dict(
            ufo1, ufo2, "public.openTypeCategories", g, create_if_not_in_ufo1=True
        )

        if g in ufo1:
            ufo1[g] = ufo2[g]
        else:
            ufo1.addGlyph(ufo2[g])

    if new_layout_rules.routines:
        ufo1.features.text += new_layout_rules.asFea(do_gdef=False)
