"""
Relationship calculator over the Django Gramps models.

This is a port of the relevant parts of ``gramps.gen.relationship``
(RelationshipCalculator: common-ancestor search, path collapsing and the
English relationship strings) and of ``gramps/plugins/rel/rel_fi.py``
(Finnish relationship names).

Relationship paths are strings of single-character codes, one per
generation step up towards the common ancestor:

    f / m   birth father / mother
    F / M   non-birth father / mother
    s       sibling (family without parents)
    a       common family, both parents (after collapsing)
    A       common family, non-birth
    b / c   common family, birth relation to mother only / father only
"""

from apps.core.models import Person

from .cache import TreeCache, ref_handle, type_string

MALE = Person.MALE
FEMALE = Person.FEMALE
UNKNOWN = Person.UNKNOWN

# ---------------------------------------------------------------------------
# English level tables (generated the same way as the Gramps constants)
# ---------------------------------------------------------------------------

_LEVEL_NAME = [
    "", "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
    "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
    "nineteenth", "twentieth", "twenty-first", "twenty-second",
    "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth",
    "twenty-seventh", "twenty-eighth", "twenty-ninth", "thirtieth",
    "thirty-first", "thirty-second", "thirty-third", "thirty-fourth",
    "thirty-fifth", "thirty-sixth", "thirty-seventh", "thirty-eighth",
    "thirty-ninth", "fortieth", "forty-first", "forty-second",
    "forty-third", "forty-fourth", "forty-fifth", "forty-sixth",
    "forty-seventh", "forty-eighth", "forty-ninth", "fiftieth",
]

_REMOVED_LEVEL = [
    "", " once removed", " twice removed", " three times removed",
    " four times removed", " five times removed", " six times removed",
    " seven times removed", " eight times removed", " nine times removed",
    " ten times removed", " eleven times removed", " twelve times removed",
    " thirteen times removed", " fourteen times removed",
    " fifteen times removed", " sixteen times removed",
    " seventeen times removed", " eighteen times removed",
    " nineteen times removed", " twenty times removed",
]

_MAX_LEVEL = len(_LEVEL_NAME)


def _grand_table(word, base_offset):
    """
    Build a table like Gramps' ``_FATHER_LEVEL``::

        ["", "father", "grandfather", "great grandfather",
         "second great grandfather", ...]

    ``base_offset`` is 2 for parent/child/nephew tables (level 2 is
    "grand...") and 3 for sibling/aunt/uncle tables (level 3 is "grand...").
    """
    table = [""]
    for level in range(1, _MAX_LEVEL):
        if level < base_offset:
            table.append("%%(step)s%s%%(inlaw)s" % word)
        elif level == base_offset:
            table.append("%%(step)sgrand%s%%(inlaw)s" % word)
        elif level == base_offset + 1:
            table.append("great %%(step)sgrand%s%%(inlaw)s" % word)
        else:
            table.append(
                "%s great %%(step)sgrand%s%%(inlaw)s"
                % (_LEVEL_NAME[level - base_offset], word)
            )
    return table


_FATHER_LEVEL = _grand_table("father", 2)
_MOTHER_LEVEL = _grand_table("mother", 2)
_SON_LEVEL = _grand_table("son", 2)
_DAUGHTER_LEVEL = _grand_table("daughter", 2)
_NEPHEW_LEVEL = _grand_table("nephew", 2)
_NIECE_LEVEL = _grand_table("niece", 2)
_SISTER_LEVEL = ["", "%(step)ssister%(inlaw)s"] + _grand_table("aunt", 3)[2:]
_BROTHER_LEVEL = ["", "%(step)sbrother%(inlaw)s"] + _grand_table("uncle", 3)[2:]
_SIBLING_LEVEL = ["", "%(step)ssibling%(inlaw)s"] + _grand_table("uncle/aunt", 3)[2:]


# Finnish strings for partner relationships (from the Gramps fi.po).
_FI_PARTNER = {
    ("husband", ""): "aviomies",
    ("wife", ""): "vaimo",
    ("spouse", "gender unknown"): "puoliso",
    ("ex-husband", ""): "ent. aviomies",
    ("ex-wife", ""): "ent. vaimo",
    ("ex-spouse", "gender unknown"): "ent. puoliso",
    ("partner", "male,unmarried"): "avomies",
    ("partner", "female,unmarried"): "avovaimo",
    ("partner", "gender unknown,unmarried"): "avopuoliso",
    ("ex-partner", "male,unmarried"): "ent. avomies",
    ("ex-partner", "female,unmarried"): "ent. avovaimo",
    ("ex-partner", "gender unknown,unmarried"): "ent. avopuoliso",
    ("partner", "male,civil union"): "virallinen mieskumppani",
    ("partner", "female,civil union"): "virallinen naiskumppani",
    ("partner", "gender unknown,civil union"): "virallinen kumppani",
    ("former partner", "male,civil union"): "virallinen ent. mieskumppani",
    ("former partner", "female,civil union"): "virallinen ent. naiskumppani",
    ("former partner", "gender unknown,civil union"): "virallinen ent. kumppani",
    ("partner", "male,unknown relation"): "mieskumppani, suhde tuntematon",
    ("partner", "female,unknown relation"): "naiskumppani, suhde tuntematon",
    ("partner", "gender unknown,unknown relation"): "kumppani, sukupuoli ja suhde tuntematon",
    ("former partner", "male,unknown relation"): "ent. mieskumppani, suhde tuntematon",
    ("former partner", "female,unknown relation"): "ent. naiskumppani, suhde tuntematon",
    ("former partner", "gender unknown,unknown relation"): "ent. kumppani, sukupuoli ja suhde tuntematon",
}

CHILD_REL_BIRTH = "Birth"
CHILD_REL_UNKNOWN = "Unknown"


def _child_relations(family, person_handle):
    """
    Return (mother_relation, father_relation) of a child in a family, or
    None when the person is not a child of the family.
    """
    for ref in family.child_ref_list or []:
        if ref_handle(ref) == person_handle:
            if isinstance(ref, dict):
                mrel = type_string(ref.get("mrel"), CHILD_REL_BIRTH) or CHILD_REL_BIRTH
                frel = type_string(ref.get("frel"), CHILD_REL_BIRTH) or CHILD_REL_BIRTH
            else:
                mrel = frel = CHILD_REL_BIRTH
            return (mrel, frel)
    return None


class RelationshipCalculator:
    """English relationship calculator (port of gramps.gen.relationship)."""

    REL_MOTHER = "m"
    REL_FATHER = "f"
    REL_MOTHER_NOTBIRTH = "M"
    REL_FATHER_NOTBIRTH = "F"
    REL_SIBLING = "s"
    REL_FAM_BIRTH = "a"
    REL_FAM_NONBIRTH = "A"
    REL_FAM_BIRTH_MOTH_ONLY = "b"
    REL_FAM_BIRTH_FATH_ONLY = "c"

    NORM_SIB = 0
    HALF_SIB_MOTHER = 1
    HALF_SIB_FATHER = 2
    STEP_SIB = 3
    UNKNOWN_SIB = 4

    STEP = "step"
    HALF = "half-"
    INLAW = "-in-law"

    PARTNER_MARRIED = 1
    PARTNER_UNMARRIED = 2
    PARTNER_CIVIL_UNION = 3
    PARTNER_UNKNOWN_REL = 4
    PARTNER_EX_MARRIED = 5
    PARTNER_EX_UNMARRIED = 6
    PARTNER_EX_CIVIL_UNION = 7
    PARTNER_EX_UNKNOWN_REL = 8

    lang = "en"

    def __init__(self, db=None, depth=15):
        self.db = db or TreeCache()
        self.depth = depth
        self._max_depth_reached = False
        self._loop_detected = False
        self._max_depth = depth
        self._all_families = False
        self._all_dist = False
        self._only_birth = False
        self._crosslinks = False
        self._msg = []

    def set_depth(self, depth):
        self.depth = depth

    def get_depth(self):
        return self.depth

    # ----------------------------------------------------------------- English
    def _get_father(self, level, step="", inlaw=""):
        if level > len(_FATHER_LEVEL) - 1:
            return "distant %sancestor%s (%d generations)" % (step, inlaw, level)
        return _FATHER_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_son(self, level, step="", inlaw=""):
        if level > len(_SON_LEVEL) - 1:
            return "distant %sdescendant%s (%d generations)" % (step, inlaw, level)
        return _SON_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_mother(self, level, step="", inlaw=""):
        if level > len(_MOTHER_LEVEL) - 1:
            return "distant %sancestor%s (%d generations)" % (step, inlaw, level)
        return _MOTHER_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_daughter(self, level, step="", inlaw=""):
        if level > len(_DAUGHTER_LEVEL) - 1:
            return "distant %sdescendant%s (%d generations)" % (step, inlaw, level)
        return _DAUGHTER_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_parent_unknown(self, level, step="", inlaw=""):
        if level < len(_LEVEL_NAME):
            return _LEVEL_NAME[level] + " " + "%sancestor%s" % (step, inlaw)
        return "distant %sancestor%s (%d generations)" % (step, inlaw, level)

    def _get_child_unknown(self, level, step="", inlaw=""):
        if level < len(_LEVEL_NAME):
            return _LEVEL_NAME[level] + " " + "%sdescendant%s" % (step, inlaw)
        return "distant %sdescendant (%d generations)" % (step, level)

    def _get_aunt(self, level, step="", inlaw=""):
        if level > len(_SISTER_LEVEL) - 1:
            return "distant %saunt%s" % (step, inlaw)
        return _SISTER_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_uncle(self, level, step="", inlaw=""):
        if level > len(_BROTHER_LEVEL) - 1:
            return "distant %suncle%s" % (step, inlaw)
        return _BROTHER_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_nephew(self, level, step="", inlaw=""):
        if level > len(_NEPHEW_LEVEL) - 1:
            return "distant %snephew%s" % (step, inlaw)
        return _NEPHEW_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_niece(self, level, step="", inlaw=""):
        if level > len(_NIECE_LEVEL) - 1:
            return "distant %sniece%s" % (step, inlaw)
        return _NIECE_LEVEL[level] % {"step": step, "inlaw": inlaw}

    def _get_cousin(self, level, removed, direction="", step="", inlaw=""):
        if removed == 0 and level < len(_LEVEL_NAME):
            return "%s %scousin%s" % (_LEVEL_NAME[level], step, inlaw)
        if removed > len(_REMOVED_LEVEL) - 1 or level > len(_LEVEL_NAME) - 1:
            return "distant %srelative%s" % (step, inlaw)
        return "%s %scousin%s%s%s" % (
            _LEVEL_NAME[level], step, inlaw, _REMOVED_LEVEL[removed], direction,
        )

    def _get_sibling(self, level, step="", inlaw=""):
        if level < len(_SIBLING_LEVEL):
            return _SIBLING_LEVEL[level] % {"step": step, "inlaw": inlaw}
        return "distant %suncle/aunt%s" % (step, inlaw)

    # ------------------------------------------------------------ parents etc.
    def get_birth_parents(self, person):
        """Return (birth mother handle, birth father handle)."""
        birthfather = None
        birthmother = None
        for fam_handle in person.parent_family_list or []:
            family = self.db.get_family(fam_handle)
            if not family:
                continue
            childrel = _child_relations(family, person.handle)
            if not childrel:
                continue
            if not birthmother and childrel[0] == CHILD_REL_BIRTH:
                birthmother = family.mother_handle_id
            if not birthfather and childrel[1] == CHILD_REL_BIRTH:
                birthfather = family.father_handle_id
            if birthmother and birthfather:
                break
        return (birthmother, birthfather)

    def _get_nonbirth_parent_list(self, person):
        nb_parents = []
        for fam_handle in person.parent_family_list or []:
            family = self.db.get_family(fam_handle)
            if not family:
                continue
            childrel = _child_relations(family, person.handle)
            if not childrel:
                continue
            if childrel[0] not in (CHILD_REL_BIRTH, CHILD_REL_UNKNOWN):
                nb_parents.append(family.mother_handle_id)
            if childrel[1] not in (CHILD_REL_BIRTH, CHILD_REL_UNKNOWN):
                nb_parents.append(family.father_handle_id)
        return list(set(nb_parents))

    def get_sibling_type(self, orig, other):
        motherorig, fatherorig = self.get_birth_parents(orig)
        motherother, fatherother = self.get_birth_parents(other)
        if fatherorig and motherorig and fatherother and motherother:
            if fatherother == fatherorig and motherother == motherorig:
                return self.NORM_SIB
            if fatherother == fatherorig:
                return self.HALF_SIB_FATHER
            if motherother == motherorig:
                return self.HALF_SIB_MOTHER
            return self.STEP_SIB
        orig_nb_par = self._get_nonbirth_parent_list(orig)
        if fatherother and fatherother in orig_nb_par:
            if motherother and motherother == motherorig:
                return self.HALF_SIB_MOTHER
            return self.STEP_SIB
        if motherother and motherother in orig_nb_par:
            if fatherother and fatherother == fatherorig:
                return self.HALF_SIB_FATHER
            return self.STEP_SIB
        other_nb_par = self._get_nonbirth_parent_list(other)
        if fatherorig and fatherorig in other_nb_par:
            if motherorig and motherother == motherorig:
                return self.HALF_SIB_MOTHER
            return self.STEP_SIB
        if motherorig and motherorig in other_nb_par:
            if fatherother and fatherother == fatherorig:
                return self.HALF_SIB_FATHER
            return self.STEP_SIB
        return self.UNKNOWN_SIB

    # ----------------------------------------------------------------- spouse
    def _get_spouse_type(self, orig, other, all_rel=False):
        val = []
        for family_handle in orig.family_list or []:
            family = self.db.get_family(family_handle)
            if not family:
                continue
            if other.handle not in (family.father_handle_id, family.mother_handle_id):
                continue
            family_rel = type_string(family.type, "Married")
            ex = False
            for _ref, event in self.db.get_family_events(family):
                if event.type in ("Divorce", "Annulment"):
                    ex = True
                    break
            if family_rel == "Married":
                val.append(self.PARTNER_EX_MARRIED if ex else self.PARTNER_MARRIED)
            elif family_rel == "Unmarried":
                val.append(self.PARTNER_EX_UNMARRIED if ex else self.PARTNER_UNMARRIED)
            elif family_rel == "Civil Union":
                val.append(self.PARTNER_EX_CIVIL_UNION if ex else self.PARTNER_CIVIL_UNION)
            else:
                val.append(self.PARTNER_EX_UNKNOWN_REL if ex else self.PARTNER_UNKNOWN_REL)
        if all_rel:
            return val
        return val[-1] if val else None

    def is_spouse(self, orig, other, all_rel=False):
        spouse_type = self._get_spouse_type(orig, other, all_rel)
        if spouse_type:
            return self.get_partner_relationship_string(
                spouse_type, orig.gender, other.gender
            )
        return None

    def _partner_text(self, msgid, context=""):
        return msgid

    def get_partner_relationship_string(self, spouse_type, gender_a, gender_b):
        gender = gender_b
        if not spouse_type:
            return ""
        t = self._partner_text
        if spouse_type == self.PARTNER_MARRIED:
            if gender == MALE:
                return t("husband")
            if gender == FEMALE:
                return t("wife")
            return t("spouse", "gender unknown")
        if spouse_type == self.PARTNER_EX_MARRIED:
            if gender == MALE:
                return t("ex-husband")
            if gender == FEMALE:
                return t("ex-wife")
            return t("ex-spouse", "gender unknown")
        if spouse_type == self.PARTNER_UNMARRIED:
            if gender == MALE:
                return t("partner", "male,unmarried")
            if gender == FEMALE:
                return t("partner", "female,unmarried")
            return t("partner", "gender unknown,unmarried")
        if spouse_type == self.PARTNER_EX_UNMARRIED:
            if gender == MALE:
                return t("ex-partner", "male,unmarried")
            if gender == FEMALE:
                return t("ex-partner", "female,unmarried")
            return t("ex-partner", "gender unknown,unmarried")
        if spouse_type == self.PARTNER_CIVIL_UNION:
            if gender == MALE:
                return t("partner", "male,civil union")
            if gender == FEMALE:
                return t("partner", "female,civil union")
            return t("partner", "gender unknown,civil union")
        if spouse_type == self.PARTNER_EX_CIVIL_UNION:
            if gender == MALE:
                return t("former partner", "male,civil union")
            if gender == FEMALE:
                return t("former partner", "female,civil union")
            return t("former partner", "gender unknown,civil union")
        if spouse_type == self.PARTNER_UNKNOWN_REL:
            if gender == MALE:
                return t("partner", "male,unknown relation")
            if gender == FEMALE:
                return t("partner", "female,unknown relation")
            return t("partner", "gender unknown,unknown relation")
        if gender == MALE:
            return t("former partner", "male,unknown relation")
        if gender == FEMALE:
            return t("former partner", "female,unknown relation")
        return t("former partner", "gender unknown,unknown relation")

    # --------------------------------------------------------- distance search
    def get_relationship_distance_new(
        self, orig_person, other_person, all_families=False, all_dist=False,
        only_birth=True,
    ):
        """
        Find common ancestors of two people.

        Returns ``(data, msg)`` where data is either a single tuple
        ``(rank, ancestor_handle, path_orig, fam_orig, path_other, fam_other)``
        or, with ``all_dist``, a list of such tuples sorted by rank.
        Rank is -1 when the people are not related.
        """
        self._max_depth_reached = False
        self._loop_detected = False
        self._max_depth = self.get_depth()
        self._all_families = all_families
        self._all_dist = all_dist
        self._only_birth = only_birth
        self._crosslinks = False
        self._msg = []

        common = []
        first_map = {}
        second_map = {}
        try:
            self._apply_filter(orig_person, "", [], first_map)
            self._apply_filter(other_person, "", [], second_map, stoprecursemap=first_map)
        except RecursionError:
            return (-1, None, -1, [], -1, []), ["Relationship loop detected"] + self._msg

        for person_handle in second_map:
            if person_handle not in first_map:
                continue
            com = []
            for rel1, fam1 in zip(first_map[person_handle][0], first_map[person_handle][1]):
                len1 = len(rel1)
                for rel2, fam2 in zip(
                    second_map[person_handle][0], second_map[person_handle][1]
                ):
                    com.append((len1 + len(rel2), person_handle, rel1, fam1, rel2, fam2))
            pos = 0
            for ranknew, handlenew, rel1new, fam1new, rel2new, fam2new in com:
                insert = True
                for rank, _handle, rel1, _fam1, rel2, _fam2 in common:
                    if ranknew < rank:
                        break
                    if rel1 == rel1new[: len(rel1)] and rel2 == rel2new[: len(rel2)]:
                        insert = False
                        break
                    pos += 1
                if insert:
                    entry = (ranknew, handlenew, rel1new, fam1new, rel2new, fam2new)
                    if common:
                        common.insert(pos, entry)
                    else:
                        common = [entry]
                    deletelist = []
                    index = pos + 1
                    for _rank, _handle, rel1, _fam1, rel2, _fam2 in common[pos + 1:]:
                        if rel1new == rel1[: len(rel1new)] and rel2new == rel2[: len(rel2new)]:
                            deletelist.append(index)
                        index += 1
                    for index in reversed(deletelist):
                        del common[index]

        if self._max_depth_reached:
            self._msg.append(
                "Family Tree reaches back more than the maximum %d generations "
                "searched. It is possible that relationships have been missed"
                % self._max_depth
            )

        if common and not self._all_dist:
            return common[0], self._msg
        if common:
            return common, self._msg
        if not self._all_dist:
            return (-1, None, "", [], "", []), self._msg
        return [(-1, None, "", [], "", [])], self._msg

    def _apply_filter(self, person, rel_str, rel_fam, pmap, depth=1, stoprecursemap=None):
        if person is None or not person.handle:
            return
        if depth > self._max_depth:
            self._max_depth_reached = True
            return
        depth += 1

        commonancestor = False
        store = True
        if stoprecursemap:
            store = False
            if person.handle in stoprecursemap:
                commonancestor = True
                store = True

        if person.handle in pmap:
            if not stoprecursemap:
                self._crosslinks = True
            pmap[person.handle][0] += [rel_str]
            pmap[person.handle][1] += [rel_fam]
            for rel1 in pmap[person.handle][0]:
                for rel2 in pmap[person.handle][0]:
                    if len(rel1) < len(rel2) and rel1 == rel2[: len(rel1)]:
                        self._loop_detected = True
                        self._msg.append(
                            "Relationship loop detected: person %s connects to "
                            "himself via %s" % (person.handle, rel2[len(rel1):])
                        )
                        return
        elif store:
            pmap[person.handle] = [[rel_str], [rel_fam]]

        if commonancestor and not self._crosslinks:
            return

        parent_families = person.parent_family_list or []
        family_handles = parent_families[:1]
        if self._all_families:
            family_handles = list(parent_families)
        self.db.preload_families(family_handles)

        parentstodo = {}
        fam = 0
        for family_handle in family_handles:
            rel_fam_new = rel_fam + [fam]
            family = self.db.get_family(family_handle)
            if not family:
                continue
            childrel = _child_relations(family, person.handle)
            if not childrel:
                continue
            fhandle = family.father_handle_id
            mhandle = family.mother_handle_id
            for handle, birth_code, nonbirth_code, relation in (
                (fhandle, self.REL_FATHER, self.REL_FATHER_NOTBIRTH, childrel[1]),
                (mhandle, self.REL_MOTHER, self.REL_MOTHER_NOTBIRTH, childrel[0]),
            ):
                if handle and handle not in parentstodo:
                    persontodo = self.db.get_person(handle)
                    if relation == CHILD_REL_BIRTH:
                        addstr = birth_code
                    elif not self._only_birth:
                        addstr = nonbirth_code
                    else:
                        addstr = ""
                    if addstr and persontodo is not None:
                        parentstodo[handle] = (persontodo, rel_str + addstr, rel_fam_new)
                elif handle and handle in parentstodo:
                    famlist = parentstodo[handle][2]
                    if not isinstance(famlist[-1], list) and fam != famlist[-1]:
                        famlist = famlist[:-1] + [[famlist[-1]]]
                    if isinstance(famlist[-1], list) and fam not in famlist[-1]:
                        famlist = famlist[:-1] + [famlist[-1] + [fam]]
                        parentstodo[handle] = (
                            parentstodo[handle][0], parentstodo[handle][1], famlist,
                        )
            if not fhandle and not mhandle and stoprecursemap is None:
                # family without parents: register the siblings
                addstr = self.REL_SIBLING
                for ref in family.child_ref_list or []:
                    chandle = ref_handle(ref)
                    if not chandle or chandle == person.handle:
                        continue
                    if chandle in pmap:
                        pmap[chandle][0] += [rel_str + addstr]
                        pmap[chandle][1] += [rel_fam_new]
                    else:
                        pmap[chandle] = [[rel_str + addstr], [rel_fam_new]]
            fam += 1

        for _handle, data in parentstodo.items():
            self._apply_filter(data[0], data[1], data[2], pmap, depth, stoprecursemap)

    # ---------------------------------------------------------------- collapse
    def collapse_relations(self, relations):
        if relations[0][0] == -1:
            return relations
        commonnew = []
        existing_path = []
        for relation in relations:
            relstrfirst = None
            commonhandle = [relation[1]]
            if relation[2]:
                relstrfirst = relation[2][:-1]
            relstrsec = None
            if relation[4]:
                relstrsec = relation[4][:-1]
            relfamfirst = relation[3][:]
            relfamsec = relation[5][:]
            rela2 = relation[2]
            rela4 = relation[4]
            if relation[2] and relation[2][-1] == self.REL_SIBLING:
                rela2 = relation[2][:-1] + self.REL_FAM_BIRTH
                rela4 = relation[4] + self.REL_FAM_BIRTH
                relfamsec = relfamsec + [relfamfirst[-1]]
                relstrsec = relation[4][:-1]
                commonhandle = []

            familypaths = []
            if relfamfirst and isinstance(relfamfirst[-1], list):
                if relfamsec and isinstance(relfamsec[-1], list):
                    for val1 in relfamfirst[-1]:
                        for val2 in relfamsec[-1]:
                            familypaths.append(
                                (relstrfirst, relstrsec,
                                 relfamfirst[:-1] + [val1], relfamsec[:-1] + [val2])
                            )
                else:
                    for val1 in relfamfirst[-1]:
                        familypaths.append(
                            (relstrfirst, relstrsec, relfamfirst[:-1] + [val1], relfamsec)
                        )
            elif relfamsec and isinstance(relfamsec[-1], list):
                for val2 in relfamsec[-1]:
                    familypaths.append(
                        (relstrfirst, relstrsec, relfamfirst, relfamsec[:-1] + [val2])
                    )
            else:
                familypaths.append((relstrfirst, relstrsec, relfamfirst, relfamsec))
            for familypath in familypaths:
                try:
                    posfam = existing_path.index(familypath)
                except ValueError:
                    posfam = None
                if posfam is not None and relstrfirst is not None and relstrsec is not None:
                    tmp = commonnew[posfam]
                    newcomstra = self._famrel_from_persrel(rela2[-1], tmp[2][-1])
                    newcomstrb = self._famrel_from_persrel(rela4[-1], tmp[4][-1])
                    commonnew[posfam] = (
                        tmp[0], tmp[1] + commonhandle,
                        rela2[:-1] + newcomstra, tmp[3],
                        rela4[:-1] + newcomstrb, tmp[5],
                    )
                else:
                    existing_path.append(familypath)
                    commonnew.append(
                        (relation[0], commonhandle, rela2, familypath[2], rela4, familypath[3])
                    )
        collapsed = commonnew[:1]
        for rel in commonnew[1:]:
            found = False
            for newrel in collapsed:
                if newrel[0:3] == rel[0:3] and newrel[4] == rel[4]:
                    path1 = []
                    path2 = []
                    for a, b in zip(newrel[3], rel[3]):
                        if a == b:
                            path1.append(a)
                        elif isinstance(a, list):
                            path1.append(a + [b])
                        else:
                            path1.append([a, b])
                    for a, b in zip(newrel[5], rel[5]):
                        if a == b:
                            path2.append(a)
                        elif isinstance(a, list):
                            path2.append(a + [b])
                        else:
                            path2.append([a, b])
                    newrel[3][:] = path1[:]
                    newrel[5][:] = path2[:]
                    found = True
                    break
            if not found:
                collapsed.append(rel)
        return collapsed

    def _famrel_from_persrel(self, persrela, persrelb):
        if persrela == persrelb:
            return persrela
        pair = {persrela, persrelb}
        if pair == {self.REL_MOTHER, self.REL_FATHER}:
            return self.REL_FAM_BIRTH
        if pair == {self.REL_MOTHER, self.REL_FATHER_NOTBIRTH}:
            return self.REL_FAM_BIRTH_MOTH_ONLY
        if pair == {self.REL_FATHER, self.REL_MOTHER_NOTBIRTH}:
            return self.REL_FAM_BIRTH_FATH_ONLY
        fam_codes = (
            self.REL_FAM_BIRTH, self.REL_FAM_BIRTH_FATH_ONLY,
            self.REL_FAM_BIRTH_MOTH_ONLY, self.REL_FAM_NONBIRTH,
        )
        if persrela in fam_codes:
            return persrela
        if persrelb in fam_codes:
            return persrelb
        return self.REL_FAM_NONBIRTH

    def only_birth(self, path):
        for value in path:
            if value in (self.REL_FAM_NONBIRTH, self.REL_FATHER_NOTBIRTH, self.REL_MOTHER_NOTBIRTH):
                return False
        return True

    # ------------------------------------------------------------- public API
    def _relationship_string_for(self, orig_person, other_person, rel):
        dist_orig = len(rel[2])
        dist_other = len(rel[4])
        birth = self.only_birth(rel[2]) and self.only_birth(rel[4])
        if dist_orig == dist_other == 1:
            return self.get_sibling_relationship_string(
                self.get_sibling_type(orig_person, other_person),
                orig_person.gender, other_person.gender,
            )
        return self.get_single_relationship_string(
            dist_orig, dist_other, orig_person.gender, other_person.gender,
            rel[2], rel[4], only_birth=birth, in_law_a=False, in_law_b=False,
        )

    def get_one_relationship(self, orig_person, other_person, extra_info=False):
        """
        Most relevant relationship between two people.

        With ``extra_info`` returns ``(string, dist_orig, dist_other)``.
        """
        if orig_person is None or other_person is None:
            rel_str = "undefined"
            return (rel_str, -1, -1) if extra_info else rel_str
        if orig_person.handle == other_person.handle:
            return ("", -1, -1) if extra_info else ""
        is_spouse = self.is_spouse(orig_person, other_person)
        if is_spouse:
            return (is_spouse, -1, -1) if extra_info else is_spouse

        data, _msg = self.get_relationship_distance_new(
            orig_person, other_person, all_dist=True, all_families=True, only_birth=False,
        )
        if data[0][0] == -1:
            return ("", -1, -1) if extra_info else ""

        data = self.collapse_relations(data)
        databest = [data[0]]
        rankbest = data[0][0]
        for rel in data:
            if rel[0] == rankbest:
                databest.append(rel)
        rel = databest[0]
        if len(databest) > 1:
            order = [
                self.REL_FAM_BIRTH, self.REL_FAM_BIRTH_MOTH_ONLY,
                self.REL_FAM_BIRTH_FATH_ONLY, self.REL_MOTHER, self.REL_FATHER,
                self.REL_SIBLING, self.REL_FAM_NONBIRTH, self.REL_MOTHER_NOTBIRTH,
                self.REL_FATHER_NOTBIRTH,
            ]
            orderbest = order.index(self.REL_MOTHER)
            for relother in databest:
                relbirth = self.only_birth(rel[2]) and self.only_birth(rel[4])
                if relother[2] == "" or relother[4] == "":
                    rel = relother
                    break
                if not relbirth and self.only_birth(relother[2]) and self.only_birth(relother[4]):
                    rel = relother
                    continue
                if (
                    order.index(relother[2][-1]) < order.index(rel[2][-1])
                    and order.index(relother[2][-1]) < orderbest
                ):
                    rel = relother
                    continue
                if (
                    order.index(relother[4][-1]) < order.index(rel[4][-1])
                    and order.index(relother[4][-1]) < orderbest
                ):
                    rel = relother
                    continue
                if order.index(rel[2][-1]) < orderbest or order.index(rel[4][-1]) < orderbest:
                    continue
                if order.index(relother[2][-1]) < order.index(rel[2][-1]):
                    rel = relother
                    continue
                if (
                    order.index(relother[2][-1]) == order.index(rel[2][-1])
                    and order.index(relother[4][-1]) < order.index(rel[4][-1])
                ):
                    rel = relother
                    continue
        rel_str = self._relationship_string_for(orig_person, other_person, rel)
        if extra_info:
            return (rel_str, len(rel[2]), len(rel[4]))
        return rel_str

    def get_all_relationships(self, orig_person, other_person):
        """
        Return ``(relationship strings, common ancestor handle lists)`` for
        all relationships between two people.
        """
        relstrings = []
        commons = {}
        if orig_person is None or other_person is None:
            return ([], [])
        if orig_person.handle == other_person.handle:
            return ([], [])
        is_spouse = self.is_spouse(orig_person, other_person)
        if is_spouse:
            relstrings.append(is_spouse)
            commons[is_spouse] = []
        data, _msg = self.get_relationship_distance_new(
            orig_person, other_person, all_dist=True, all_families=True, only_birth=False,
        )
        if data[0][0] != -1:
            data = self.collapse_relations(data)
            for rel in data:
                rel2 = rel[2]
                rel4 = rel[4]
                rel1 = rel[1]
                dist_orig = len(rel[2])
                dist_other = len(rel[4])
                if rel[2] and rel[2][-1] == self.REL_SIBLING:
                    rel2 = rel2[:-1] + self.REL_FAM_BIRTH
                    dist_other += 1
                    rel4 = rel4 + self.REL_FAM_BIRTH
                    rel1 = None
                birth = self.only_birth(rel2) and self.only_birth(rel4)
                if dist_orig == dist_other == 1:
                    rel_str = self.get_sibling_relationship_string(
                        self.get_sibling_type(orig_person, other_person),
                        orig_person.gender, other_person.gender,
                    )
                else:
                    rel_str = self.get_single_relationship_string(
                        dist_orig, dist_other, orig_person.gender, other_person.gender,
                        rel2, rel4, only_birth=birth, in_law_a=False, in_law_b=False,
                    )
                if rel_str not in relstrings:
                    relstrings.append(rel_str)
                    commons[rel_str] = list(rel1) if rel1 else []
                elif rel1:
                    commons[rel_str].extend(rel1)
        return (relstrings, [commons[s] for s in relstrings])

    # ------------------------------------------------------- English strings
    def get_single_relationship_string(
        self, Ga, Gb, gender_a, gender_b, reltocommon_a, reltocommon_b,
        only_birth=True, in_law_a=False, in_law_b=False,
    ):
        step = "" if only_birth else self.STEP
        inlaw = self.INLAW if (in_law_a or in_law_b) else ""
        rel_str = "distant %srelative%s" % (step, inlaw)
        if Ga == 0:
            if Gb == 0:
                rel_str = "same person"
            elif gender_b == MALE:
                rel_str = self._get_son(Gb, step, inlaw)
            elif gender_b == FEMALE:
                rel_str = self._get_daughter(Gb, step, inlaw)
            else:
                rel_str = self._get_child_unknown(Gb, step, inlaw)
        elif Gb == 0:
            if gender_b == MALE:
                rel_str = self._get_father(Ga, step, inlaw)
            elif gender_b == FEMALE:
                rel_str = self._get_mother(Ga, step, inlaw)
            else:
                rel_str = self._get_parent_unknown(Ga, step, inlaw)
        elif Gb == 1:
            if gender_b == MALE:
                rel_str = self._get_uncle(Ga, step, inlaw)
            elif gender_b == FEMALE:
                rel_str = self._get_aunt(Ga, step, inlaw)
            else:
                rel_str = self._get_sibling(Ga, step, inlaw)
        elif Ga == 1:
            if gender_b == MALE:
                rel_str = self._get_nephew(Gb - 1, step, inlaw)
            elif gender_b == FEMALE:
                rel_str = self._get_niece(Gb - 1, step, inlaw)
            elif Gb < len(_NIECE_LEVEL) and Gb < len(_NEPHEW_LEVEL):
                rel_str = "%s or %s" % (
                    self._get_nephew(Gb - 1, step, inlaw),
                    self._get_niece(Gb - 1, step, inlaw),
                )
            else:
                rel_str = "distant %snephews/nieces%s" % (step, inlaw)
        elif Ga == Gb:
            rel_str = self._get_cousin(Ga - 1, 0, direction="", step=step, inlaw=inlaw)
        elif Ga > Gb:
            rel_str = self._get_cousin(Gb - 1, Ga - Gb, direction=" (up)", step=step, inlaw=inlaw)
        else:
            rel_str = self._get_cousin(Ga - 1, Gb - Ga, direction=" (down)", step=step, inlaw=inlaw)
        return rel_str

    def get_sibling_relationship_string(
        self, sib_type, gender_a, gender_b, in_law_a=False, in_law_b=False
    ):
        if sib_type in (self.NORM_SIB, self.UNKNOWN_SIB):
            typestr = ""
        elif sib_type in (self.HALF_SIB_MOTHER, self.HALF_SIB_FATHER):
            typestr = self.HALF
        else:
            typestr = self.STEP
        inlaw = self.INLAW if (in_law_a or in_law_b) else ""
        if gender_b == MALE:
            return self._get_uncle(1, typestr, inlaw)
        if gender_b == FEMALE:
            return self._get_aunt(1, typestr, inlaw)
        return self._get_sibling(1, typestr, inlaw)


class FinnishRelationshipCalculator(RelationshipCalculator):
    """Finnish relationship names (port of gramps/plugins/rel/rel_fi.py)."""

    lang = "fi"

    _parents_level = [
        "", "vanhemmat", "isovanhemmat", "isoisovanhemmat",
        "isoisoisovanhemmat", "isoisoisoisovanhemmat",
    ]

    def _partner_text(self, msgid, context=""):
        return _FI_PARTNER.get((msgid, context), msgid)

    def get_cousin(self, level):
        if level == 0:
            return ""
        if level == 1:
            return "serkku"
        if level == 2:
            return "pikkuserkku"
        return "%d. serkku" % level

    def get_cousin_genitive(self, level):
        if level == 0:
            return ""
        if level == 1:
            return "serkun"
        if level == 2:
            return "pikkuserkun"
        return "%d. serkun" % level

    def get_parents(self, level):
        if level > len(self._parents_level) - 1:
            return "kaukaiset esivanhemmat"
        return self._parents_level[level]

    def get_direct_ancestor(self, gender, rel_string):
        result = []
        for ix in range(len(rel_string) - 1):
            result.append("isän" if rel_string[ix] == "f" else "äidin")
        result.append("isä" if gender == MALE else "äiti")
        return " ".join(result)

    def get_direct_descendant(self, gender, rel_string):
        result = []
        for ix in range(len(rel_string) - 2, -1, -1):
            if rel_string[ix] == "f":
                result.append("pojan")
            elif rel_string[ix] == "m":
                result.append("tyttären")
            else:
                result.append("lapsen")
        if gender == MALE:
            result.append("poika")
        elif gender == FEMALE:
            result.append("tytär")
        else:
            result.append("lapsi")
        return " ".join(result)

    def get_ancestors_cousin(self, rel_string_long, rel_string_short):
        result = []
        removed = len(rel_string_long) - len(rel_string_short)
        level = len(rel_string_short) - 1
        for ix in range(removed):
            result.append("isän" if rel_string_long[ix] == "f" else "äidin")
        result.append(self.get_cousin(level))
        return " ".join(result)

    def get_cousins_descendant(self, gender, rel_string_long, rel_string_short):
        result = []
        removed = len(rel_string_long) - len(rel_string_short) - 1
        level = len(rel_string_short) - 1
        if level:
            result.append(self.get_cousin_genitive(level))
        elif rel_string_long[removed] == "f":
            result.append("veljen")
        else:
            result.append("sisaren")
        for ix in range(removed - 1, -1, -1):
            if rel_string_long[ix] == "f":
                result.append("pojan")
            elif rel_string_long[ix] == "m":
                result.append("tyttären")
            else:
                result.append("lapsen")
        if gender == MALE:
            result.append("poika")
        elif gender == FEMALE:
            result.append("tytär")
        else:
            result.append("lapsi")
        return " ".join(result)

    def get_ancestors_brother(self, rel_string):
        result = []
        for ix in range(len(rel_string) - 1):
            result.append("isän" if rel_string[ix] == "f" else "äidin")
        result.append("veli")
        return " ".join(result)

    def get_ancestors_sister(self, rel_string):
        result = []
        for ix in range(len(rel_string) - 1):
            result.append("isän" if rel_string[ix] == "f" else "äidin")
        result.append("sisar")
        return " ".join(result)

    def get_relationship(self, secondRel, firstRel, orig_gender, other_gender):
        if not firstRel:
            if not secondRel:
                return ""
            return self.get_direct_ancestor(other_gender, secondRel)
        if not secondRel:
            return self.get_direct_descendant(other_gender, firstRel)
        if len(firstRel) == 1:
            if other_gender == MALE:
                return self.get_ancestors_brother(secondRel)
            return self.get_ancestors_sister(secondRel)
        if len(secondRel) >= len(firstRel):
            return self.get_ancestors_cousin(secondRel, firstRel)
        return self.get_cousins_descendant(other_gender, firstRel, secondRel)

    def get_single_relationship_string(
        self, Ga, Gb, gender_a, gender_b, reltocommon_a, reltocommon_b,
        only_birth=True, in_law_a=False, in_law_b=False,
    ):
        return self.get_relationship(reltocommon_a, reltocommon_b, gender_a, gender_b)

    def get_sibling_relationship_string(
        self, sib_type, gender_a, gender_b, in_law_a=False, in_law_b=False
    ):
        if gender_b == MALE:
            return self.get_ancestors_brother("")
        return self.get_ancestors_sister("")


def get_calculator(db=None, lang="en", depth=15):
    """Return a relationship calculator for a language code."""
    if (lang or "en")[:2].lower() == "fi":
        return FinnishRelationshipCalculator(db, depth)
    return RelationshipCalculator(db, depth)


def get_one_relationship(db, person1, person2, depth=15, lang="en"):
    """
    Relationship string and generation distances between two people.

    Like gramps-web-api, a shallow search (depth 5) is tried first because
    deep searches are slow even when the relationship path is short.
    """
    calc = get_calculator(db, lang, depth)
    if depth > 5:
        calc.set_depth(5)
        rel_string, dist_orig, dist_other = calc.get_one_relationship(
            person1, person2, extra_info=True
        )
        if dist_orig > -1:
            return rel_string, dist_orig, dist_other
    calc.set_depth(depth)
    return calc.get_one_relationship(person1, person2, extra_info=True)
