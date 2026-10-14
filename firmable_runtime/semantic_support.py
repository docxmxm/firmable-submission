"""Finite, source-bound evidence checks; not a general semantic correctness oracle."""

import re


def plain(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# These are semantic role words, not native column aliases or dataset rules.
# Exact native identity and documentary ownership remain field_contexts' job.
SUBJECT_ROLES = {
    "supplier": r"supplier|vendor|contractor",
    "buyer": r"buyer|purchaser|procuring (?:agency|entity|organisation|organization)",
    "customer": r"customer|client",
    "licensee": r"licensee|licen[cs]e holder|licen[cs]eholder",
    "applicant": r"applicant",
    "operator": r"operator",
    "registrant": r"registrant",
    "parent": r"parent(?: company| entity| business)?",
    "subsidiary": r"subsidiary(?: company| entity| business)?",
}
ENTITY_SUBJECT = (
    r"entity|business|company|corporation|firm|organisation|organization|sole trader"
)
OTHER_SUBJECTS = {
    "venue": r"venue|premises|site|facility",
    "permit": r"permit|licen[cs]e|contract|application",
    "person": r"individual|natural person|person|contact|director|representative",
    "web_object": r"website|web site",
}


def _value_fields(ast):
    """Fields that supply output values; a conditional predicate has no owner."""
    if not isinstance(ast, dict):
        return set()
    if ast.get("op") == "field":
        return {ast["name"]}
    if ast.get("op") == "when":
        return _value_fields(ast.get("then")) | _value_fields(ast.get("else"))
    return set().union(
        *(
            _value_fields(item)
            for key, value in ast.items()
            if key != "condition"
            for item in (value if isinstance(value, list) else [value])
            if isinstance(item, dict)
        )
    )


def _owner_grammar():
    role_words = "|".join(SUBJECT_ROLES.values())
    other_words = "|".join(OTHER_SUBJECTS.values())
    atom = (
        r"(?:(?:"
        + role_words
        + r")(?: company| entity| business)?|"
        + ENTITY_SUBJECT
        + "|"
        + other_words
        + ")"
    )
    return atom, atom + r"(?: (?:(?:s|owning|and|or) )?" + atom + ")*"


def _classify_owner_phrases(owners, target=None):
    atom, _ = _owner_grammar()
    roles, other, entity = set(), set(), False
    for phrase in owners:
        nouns = [match.group() for match in re.finditer(r"\b" + atom + r"\b", phrase)]
        head = nouns[-1]
        # A disjunction/conjunction does not establish that every value is the
        # business branch. Preserve conflicting possibilities for abstention.
        alternatives = bool(re.search(r"\b(?:and|or)\b", phrase))
        owner_words = " ".join(nouns) if alternatives else head
        # The website URL is a property of its documented business owner.
        # This relationship is specific to entity.website: it cannot lend the
        # website's address/name to the company, or turn a director's/venue's
        # website into the company's website merely through an entity prefix.
        if (
            target == "entity.website"
            and not alternatives
            and len(nouns) > 1
            and re.fullmatch(OTHER_SUBJECTS["web_object"], head)
        ):
            owner_words = nouns[-2]
        roles.update(
            role
            for role, pattern in SUBJECT_ROLES.items()
            if re.search(r"\b(?:" + pattern + r")\b", phrase)
        )
        remainder = owner_words
        for pattern in SUBJECT_ROLES.values():
            remainder = re.sub(r"\b(?:" + pattern + r")\b", " ", remainder)
        other.update(
            kind
            for kind, pattern in OTHER_SUBJECTS.items()
            if re.search(r"\b(?:" + pattern + r")\b", remainder)
        )
        entity = (
            entity
            or bool(re.search(r"\b(?:" + ENTITY_SUBJECT + r")\b", remainder))
            or bool(roles)
        )
    return roles, other, entity


def _subject_words(text, target=None):
    """Recognize explicit owner vocabulary in a bound definition, not raw cells.

    A role can describe a business even when it is not its legal form. Holder
    phrases are removed before object words so a licence holder is not a licence.
    Unrecognized phrasing stays unresolved; these finite checks are not an oracle.
    """
    value = plain(text)
    _, owner = _owner_grammar()
    # Consume a complete recognized noun chain, rather than accepting an entity
    # prefix of 'business venue' or 'supplier\'s director'. The final noun is
    # the described owner; 'venue-owning company' is a company, not a venue.
    qualifier = r"(?:(?:the|a|an|current|principal|registered|credit|financial) )*"
    property_word = (
        r"(?:(?:current|principal|full|whole|complete|business|registered|postal|residential|home|street|legal|trading) )*"
        r"(?:address(?: line(?: [1-9])?)?|location|locality|suburb|city|town|state|postcode|postal code|country|name|"
        r"status|type|website|web site|url|industry(?: code)?|registration date|incorporation date|abn|acn|nzbn|"
        r"australian business number|australian company number)"
    )

    # Only an owner adjacent to the described property counts. Purpose/context
    # phrases ('company postcode used for permit administration') do not turn
    # the permit into an owner. Dictionary examples and neighboring fields are
    # already excluded by field_contexts.
    def positive_owners(pattern):
        return [
            match.group("owner")
            for match in re.finditer(pattern, value)
            if not re.search(r"\bnot (?:the |an? )?$", value[: match.start()])
        ]

    owners = positive_owners(
        r"\b"
        + property_word
        + r" (?:of|for|belonging to) "
        + qualifier
        + r"(?P<owner>"
        + owner
        + r")\b"
    )
    owners += positive_owners(
        r"\b(?P<owner>" + owner + r")(?: s)? " + property_word + r"\b"
    )
    return _classify_owner_phrases(owners, target)


def _header_subject_words(header, target=None):
    roles, other, entity = _subject_words(header, target)
    value = plain(header)
    # Keep the inherited explicit role boundary for every value-producing
    # header, even when its property word is unfamiliar ('Supplier Region').
    # This uses role vocabulary, never an exact native header/alias list.
    roles.update(
        role
        for role, pattern in SUBJECT_ROLES.items()
        if re.search(r"\b(?:" + pattern + r")\b", value)
    )
    if not other and not entity:
        _, owner = _owner_grammar()
        prefix = re.match(
            r"^(?:(?:the|current|principal|registered) )*(?P<owner>"
            + owner
            + r")\b(?= .)",
            value,
        )
        if prefix:
            found_roles, other, entity = _classify_owner_phrases(
                [prefix.group("owner")], target
            )
            roles.update(found_roles)
    return roles, other, entity or bool(roles)


def subject_support(mapping, documentation, available_fields=(), *, reviewed_name=False):
    """Keep every canonical value on one source subject without guessing an owner.

    Existing unqualified fields remain usable with an explicit unresolved status.
    An explicit foreign owner or incompatible business roles cannot be repaired
    by a checksum, a nearby identifier, or a source-level title. No cell values,
    catalogue names or neighboring field definitions participate in this check.
    A host-validated name review may resolve a header-only conflict when the
    same field's original definition explicitly identifies a business entity.
    Documentary foreign owners and mixed business roles remain hard conflicts.
    """
    target = mapping["canonical_field"]
    result = {
        "target": target,
        "source_field": mapping.get("source_field"),
        "basis": "same_field_value_subject_and_role",
        "supported": True,
        "status": "unresolved",
        "roles": [],
        "field_bindings": [],
        "limitation": "Finite source-bound owner checks detect explicit conflicts; unresolved ownership is not verified entity association or real-world truth.",
    }
    if mapping.get("claim_kind") == "unknown_abstention":
        result["status"] = "noninformative_abstention"
        return result
    if not target.startswith(("entity.", "address.")):
        return result
    all_roles, foreign, explicit = set(), set(), False
    for name in sorted(_value_fields(mapping["ast"])):
        bound = {
            "source_field": name,
            "source_fields": [name],
            "semantic_evidence": name,
        }
        _, contexts = field_contexts(bound, documentation, available_fields)
        roles, others, entity = set(), set(), False
        header_other, document_other, document_roles = set(), set(), set()
        entity_definitions = []
        for context in contexts:
            parse = (
                _header_subject_words if context["kind"] == "header" else _subject_words
            )
            found_roles, found_other, found_entity = parse(
                context.get("meaning", context["text"]), target
            )
            roles.update(found_roles)
            others.update(found_other)
            entity = entity or found_entity
            if context["kind"] == "header":
                header_other.update(found_other)
            else:
                document_other.update(found_other)
                document_roles.update(found_roles)
                if context["kind"] == "document_field_definition" and found_entity:
                    entity_definitions.append(context["line"])
        resolved_header = (
            reviewed_name
            and target in {"entity.legal_name", "entity.trading_name"}
            and bool(header_other)
            and not document_other
            and bool(entity_definitions)
            and len(document_roles) <= 1
            and len(roles) <= 1
        )
        if resolved_header:
            others = set()
        binding = {
            "source_field": name,
            "roles": sorted(roles),
            "other_subjects": sorted(others),
            "status": "foreign_subject"
            if others
            else "explicit_entity"
            if entity
            else "unresolved",
            "contexts": contexts,
        }
        if resolved_header:
            binding["reviewed_header_resolution"] = {
                "header_other_subjects": sorted(header_other),
                "document_field_definition_lines": entity_definitions,
            }
        result["field_bindings"].append(binding)
        all_roles.update(roles)
        foreign.update(others)
        explicit = explicit or entity
    result["roles"] = sorted(all_roles)
    if foreign:
        result.update(
            supported=False,
            status="foreign_subject",
            other_subjects=sorted(foreign),
            reason="Canonical entity/address values must describe the same business subject. Bound source definitions identify another object or person; omit these claims or obtain explicit same-subject evidence. An identifier in the same row does not establish association.",
        )
    elif len(all_roles) > 1:
        result.update(
            supported=False,
            status="mixed_roles",
            reason="Mixed subject roles in value-producing fields require abstention; preserve one documented business subject per observation.",
        )
    elif explicit:
        result["status"] = "explicit_entity"
    return result


def _table_cells(line):
    # Retain empty cells for explicit delimiters and character positions for
    # fixed-width text extracted from a PDF. Only a recognized header binds them.
    if "|" in line:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            stripped = stripped[1:-1]
        return [part.strip() for part in stripped.split("|")], None
    if "\t" in line:
        return [part.strip() for part in line.split("\t")], None
    matches = list(re.finditer(r"\S(?:.*?\S)?(?= {2,}|$)", line))
    return [match.group() for match in matches], [match.start() for match in matches]


def field_contexts(mapping, documentation, available_fields=()):
    """Recognize finite text definitions, never borrowing another field's cell."""
    fields = mapping.get("source_fields", [])
    primary = mapping.get("source_field") or (fields[0] if len(fields) == 1 else "")
    evidence = mapping.get("semantic_evidence", "")
    contexts = []
    if not primary:
        return primary, contexts
    if evidence == primary:
        contexts.append({"kind": "header", "text": primary})
    lines = documentation.splitlines()
    names = set(available_fields) | set(fields) | {primary}

    def clean(cell):
        return cell.strip().strip("`\"'").strip()

    def cells(line):
        return _table_cells(line)[0]

    def spellings(name):
        # Documentary spacing/word separation can hide a known native identity.
        # Such spellings reserve an ambiguous boundary only; they never rename
        # a native key or authorize a mapping to a normalized field identity.
        terms = list(re.finditer(r"\w+|[^\w\s]", name))
        variant = re.escape(terms[0].group()) if terms else re.escape(name)
        for left, right in zip(terms, terms[1:]):
            gap = (
                r"[^\S\r\n]+"
                if re.fullmatch(r"\w+", left.group())
                and re.fullmatch(r"\w+", right.group())
                else r"[^\S\r\n]*"
            )
            variant += gap + re.escape(right.group())
        # Split only boundaries present in the native spelling: camel/acronym
        # transitions and non-alphanumeric separators. A document may render
        # or omit those separators, but cannot invent a split within a word or
        # cross a line. These candidates reserve ambiguity, never aliases.
        separated = re.sub(
            r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name
        )
        words = re.findall(r"[^\W_]+", separated)
        segmented = r"(?:[^\w\r\n]|_)*".join(re.escape(word) for word in words)
        variants = [(re.escape(name), True), (variant, False)]
        if len(words) > 1:
            variants.append((segmented, False))
        return [
            (
                re.compile(
                    r"(?:"
                    + "|".join(
                        re.escape(q) + body + re.escape(q) for q in ("`", '"', "'", "")
                    )
                    + r")(?=$|\W)",
                    re.I,
                ),
                strict,
            )
            for body, strict in variants
        ]

    tokens = [(name, spellings(name)) for name in names]
    introducer = re.compile(
        r"^\s*(?:(?:column|field)\s*)?(?::|[—–-]|\b(?:is|means|contains|records|gives|denotes|represents|stores)\b)\s*(.*)$",
        re.I,
    )

    def ownership_marker(remainder):
        # An assignment or annotated definition reserves a known neighbor's
        # ownership even when our positive definition grammar cannot parse it.
        # Balanced annotations are ownership-only, never positive definitions.
        # Keep nesting/length bounded and never cross a line. Malformed or
        # excessive annotations conservatively reserve the known field rather
        # than letting their later text lend semantics to the previous owner.
        annotated = re.sub(
            r"^\s*(?:(?:column|field)\s*)?", "", remainder, count=1, flags=re.I
        )
        pairs, consumed = {"(": ")", "[": "]", "{": "}", "<": ">"}, 0
        while annotated and annotated[0] in pairs:
            stack, end = [], None
            for position, char in enumerate(annotated):
                if consumed + position >= 256 or char in "\r\n":
                    return True
                if char in pairs:
                    stack.append(pairs[char])
                    if len(stack) > 8:
                        return True
                elif char in pairs.values():
                    if not stack or char != stack.pop():
                        return True
                    if not stack:
                        end = position + 1
                        break
            if end is None:
                return True
            consumed += end
            annotated = annotated[end:].lstrip(" \t")
        # Finite definition verbs reserve ownership without expanding the
        # positive definition grammar or treating ordinary mentions as heads.
        return (
            introducer.match(annotated)
            or re.match(r"^\s*=", annotated)
            or re.match(
                r"^\s*(?:indicates|refers\s+to|identifies|holds)\b", annotated, re.I
            )
        )

    def field_head(text, whole=False, introduced=False):
        # Resolve the complete known identity before considering its delimiter.
        # Reserve the longest name at each accepted decoration position even if
        # its remainder is not a supported definition. Never fall back to a short
        # prefix. Different decoration/case interpretations remain ambiguous.
        start = len(text) - len(text.lstrip())
        starts = {start}
        if not whole:
            bullet = re.match(r"[-*]\s*", text[start:])
            if bullet:
                starts.add(start + bullet.end())
            for at in tuple(starts):
                prefix = re.match(r"(?:the|column|field)\s+", text[at:], re.I)
                if prefix:
                    starts.add(at + prefix.end())
        interpretations = set()
        for at in starts:
            candidates = []
            for name, patterns in tokens:
                for pattern, strict in patterns:
                    match = pattern.match(text, at)
                    if match and (not whole or not text[match.end() :].strip()):
                        exact = strict and match.group() in {
                            q + name + q for q in ("", "`", '"', "'")
                        }
                        candidates.append((name, match.end(), exact, strict))
            if candidates:
                longest = max(item[1] for item in candidates)
                candidates = [item for item in candidates if item[1] == longest]
                exact = [item for item in candidates if item[2]]
                candidates = exact or candidates
                owners = {item[0] for item in candidates}
                # Only an exact native spelling disambiguates distinct owners.
                # Multiple patterns for the same key are not competitors; a
                # segmented-only spelling still cannot authorize that key.
                owner = (
                    candidates[0][0]
                    if len(owners) == 1 and any(item[3] for item in candidates)
                    else None
                )
                interpretations.add((owner, longest))
        if not interpretations:
            return None
        if introduced and not any(
            ownership_marker(text[end:]) for _, end in interpretations
        ):
            return None
        return (
            next(iter(interpretations)) if len(interpretations) == 1 else (None, None)
        )

    def is_new_field(line):
        parts = cells(line)
        return bool(
            (parts and field_head(parts[0], whole=True) is not None)
            or field_head(line) is not None
            or re.match(r"^\s*[`\"'][^`\"']+[`\"']\s*:", line)
        )

    def own_clauses(text):
        end = len(text)
        for boundary in re.finditer(";", text):
            following = text[boundary.end() :]
            head = field_head(following)
            if (head is not None and head[0] != primary) or re.match(
                r"^\s*[`\"'][^`\"']+[`\"']\s*:", following
            ):
                end = boundary.start()
                break
        # A known-field definition changes ownership wherever it occurs. Exact
        # neighbour spellings with an unclassified continuation also reserve an
        # ambiguous clause; complete references and this field keep their meaning.
        consumed = 0
        for boundary in re.finditer(r"(?<!\w)(?=\S)", text[:end]):
            if boundary.start() < consumed:
                continue
            at = boundary.start()
            head = field_head(text[at:end])
            prefix = text[:at]
            subject = bool(
                re.search(
                    r"(?:^|[.;:\n])\s*(?:(?:the|column|field)\s+)?$", prefix, re.I
                )
            )
            # field_head may itself consume the decoration in "Related field".
            reference = bool(
                re.search(r"\b(?:see|refer to|related(?: field)?)\s+$", prefix, re.I)
            )
            mere_reference = False
            if head is not None and head[1] is not None:
                remainder = text[at + head[1] : end]
                # Only a complete reference-only clause is an object mention.
                # A "See" prefix cannot hide a later predicate about the neighbor.
                clause = re.split(r"[.;\n]", remainder, maxsplit=1)[0].strip()
                mere_reference = reference and bool(
                    re.fullmatch(
                        r"(?:for reference|\[reference only\]|\(see notes\)|\(see note \([a-z0-9]+\)\) for reference)",
                        clause,
                        re.I,
                    )
                )
                token = text[at : at + head[1]]
                exact = head[0] is not None and any(
                    token.endswith(q + head[0] + q) for q in ("", "`", '"', "'")
                )
                # Reserve an unfamiliar predicate without a verb list. A bare
                # word in "company status;" is not itself a neighbouring clause.
                continued = exact and re.match(r"^\s*[\w'’]", remainder)
                reserved = (
                    subject or reference or ownership_marker(remainder) or continued
                )
            else:
                reserved = head is not None
            if (
                head is not None
                and head[0] != primary
                and reserved
                and not mere_reference
            ):
                end = boundary.start()
                break
            if head is not None:
                consumed = boundary.start() + head[1]
        return text[:end]

    header = None
    header_declared = False
    for number, line in enumerate(lines):
        parts = cells(line)
        normalized = [plain(part) for part in parts]
        if not line.strip():
            header = None
            header_declared = False
            continue
        if len(parts) >= 2 and normalized[0] in {
            "field",
            "field name",
            "column",
            "column name",
            "name",
        }:
            meanings = [
                i
                for i, part in enumerate(normalized)
                if part in {"description", "definition", "meaning"}
            ]
            parameters = [
                i
                for i, part in enumerate(normalized)
                if part in {"parameters", "data parameters", "allowed values", "values"}
            ]
            _, starts = _table_cells(line)
            header = (
                len(parts),
                meanings[0] if len(parts) <= 5 and len(meanings) == 1 else None,
                starts,
                parameters[0] if len(parameters) == 1 else None,
            )
            header_declared = True
            continue
        fixed_row = False
        if header and header[2]:
            starts = header[2]
            _, row_starts = _table_cells(line)
            if (
                row_starts
                and len(row_starts) > 1
                and row_starts[1] == starts[1]
                and line[: starts[1]].strip()
            ):
                # The trailing parameter cell can be blank; slicing a matched
                # fixed-width row preserves all declared columns nonetheless.
                parts = [
                    line[
                        start : starts[i + 1] if i + 1 < len(starts) else len(line)
                    ].strip()
                    for i, start in enumerate(starts)
                ]
                fixed_row = True
        if (
            header
            and len(parts) != header[0]
            and not all(re.fullmatch(r"[:—–-]+", part) for part in parts)
        ):
            # An indented fixed-width continuation does not invalidate its
            # header. A nonempty first column is a new row, not a continuation.
            starts = header[2]
            if not starts or line[: starts[1]].strip():
                header = None
        cell_head = field_head(parts[0], whole=True) if parts else None
        bare_head = field_head(line, whole=True)
        head = field_head(line)
        matched = (
            introducer.match(line[head[1] :]) if head and head[0] == primary else None
        )
        # Splitting is only a layout hypothesis: a delimiter may be part of a
        # complete known field name. Reconcile that identity before a table cell
        # can lend evidence to its shorter prefix. Outer pipes also participate
        # in arbitration, without turning table contents into prose definitions.
        stripped = line.strip()
        wrapped = stripped.startswith("|") and stripped.endswith("|")
        row_head = field_head(stripped[1:-1]) if wrapped else head
        heads = [candidate for candidate in (head, row_head) if candidate is not None]
        identities = {candidate[0] for candidate in heads}
        if None in identities or len(identities) > 1:
            continue
        if cell_head is not None and identities and cell_head[0] not in identities:
            if header_declared or wrapped:
                # An explicit table and a complete identity disagree. Neither
                # interpretation is sufficient authority to bind this line.
                continue
            # A bare or introduced complete identity may still define its own
            # field. Unsupported remainders never fall back to a short cell.
            cell_head = None
        meaning, structured, bare, table = None, False, False, None
        if 2 <= len(parts) <= 5 and cell_head is not None:
            if cell_head[0] != primary:
                continue
            if header and header[0] == len(parts):
                meaning = parts[header[1]] if header[1] is not None else None
                if header[1] is not None:
                    # Loose two-space tables remain supported, but only truly
                    # aligned columns authorize fixed-width continuations.
                    fixed = header[2] if fixed_row else None
                    table = (header[0], header[1], fixed, header[3])
            elif (
                not header_declared
                and len(parts) in {4, 5}
                and clean(parts[2]).lower()
                in {
                    "text",
                    "string",
                    "number",
                    "integer",
                    "date",
                    "datetime",
                    "boolean",
                }
                and (len(parts) == 4 or re.fullmatch(r"[0-9]+", clean(parts[3])))
            ):
                # A PDF page break can lose the header. Recognize only this
                # finite dictionary-row grammar, never a generic three-cell row
                # or an attempted override of a header in the same text block.
                meaning = parts[1]
                _, starts = _table_cells(line)
                table = (len(parts), 1, starts, len(parts) - 1)
            elif len(parts) == 2 and not header_declared:
                meaning = parts[1]
            structured = True
        elif bare_head is not None:
            if bare_head[0] == primary:
                meaning, bare = "", True
        elif matched:
            meaning = matched.group(1)
        if meaning is None:
            continue
        span = [line]
        enum_parameters = []
        if table:
            # At most eight adjacent table lines, with an empty field column.
            # Keep the original contiguous span, but lend no type/parameter
            # cell text to the description's meaning.
            count, meaning_index, starts, parameter_index = table
            if parameter_index is not None:
                enum_parameters.append(parts[parameter_index])
            for following in lines[number + 1 : number + 9]:
                if not following.strip() or is_new_field(following):
                    break
                if starts:
                    if following[: starts[1]].strip():
                        break
                    continuation = [
                        following[
                            start : starts[i + 1] if i + 1 < count else len(following)
                        ].strip()
                        for i, start in enumerate(starts)
                    ]
                else:
                    continuation, _ = _table_cells(following)
                    if len(continuation) != count or continuation[0]:
                        break
                addition = continuation[meaning_index]
                if parameter_index is not None:
                    enum_parameters.append(continuation[parameter_index])
                span.append(following)
                if addition:
                    meaning += " " + addition
        # At most three indented continuations. A bare field may have one next
        # unindented definition line. Known adjacent fields always terminate it.
        for following in () if table else lines[number + 1 : number + 4]:
            if not following.strip() or is_new_field(following):
                break
            continuation = cells(following)
            if "|" in following or "\t" in following or len(continuation) > 1:
                break
            if not following[:1].isspace() and not (bare and len(span) == 1):
                break
            if structured and not following[:1].isspace():
                break
            span.append(following)
            meaning += " " + following.strip()
        text = "\n".join(span)
        if evidence and (evidence == primary or evidence in text or text in evidence):
            # Semicolon clauses may define enum codes, but must not lend another
            # field's meaning. Keep the original span for exact code relations.
            definition = own_clauses(meaning)
            parameters = own_clauses("\n".join(enum_parameters))
            contexts.append(
                {
                    "kind": "document_field_definition",
                    "line": number + 1,
                    "end_line": number + len(span),
                    "text": text,
                    "meaning": definition,
                    "enum_parameters": parameters,
                }
            )
    return primary, contexts


LEGAL = (
    r"\blegal (?:entity )?(?:name|title|designation|denomination)\b",
    r"\bregistered (?:(?:company|entity|corporation|firm) )?(?:name|title|designation)\b",
    r"\b(?:name|title|designation) (?:as )?(?:registered|recorded|entered) (?:in|on|with) (?:the )?(?:company|companies|corporate|entity) (?:register|registry)\b",
)
TRADING = (
    r"\b(?:trading|trade|operating) name\b",
    r"\b(?:trades|trading|doing business) as\b",
    r"\bdba\b",
    r"\bname under which (?:the )?(?:entity|business|company) (?:conducts|carries on|does) business\b",
    r"\bname used(?: or to be used)? (?:to (?:conduct|carry on|do) business|in relation to (?:one or more )?businesses?)\b",
)
REGISTERED_DATE = (
    r"\b(?:(?:entity|company|corporation|business) (?:registration|registered|incorporation) date|date of (?:entity|company|corporation) registration|incorporation date|date of incorporation)\b",
    r"\bdate (?:on which |when )?(?:the )?(?:entity|company|corporation) (?:was )?(?:registered|incorporated)\b",
    r"\bdate on which a company was registered\b",
)


def _company_register_name_support(meaning):
    # Complete assertions only: an object's company name, purpose qualifier or
    # unknown suffix must not borrow a matching phrase embedded in its text.
    name = (
        r"(?:(?:company|corporation) name|name of (?:the |a )?(?:company|corporation))"
    )
    register = r"(?:company|companies|corporate) (?:register|registry)"
    patterns = (
        name
        + r" as (?:it )?appears (?:on|in) (?:the )?(?:"
        + register
        + r"|register|registry)",
        r"name as (?:it )?appears (?:on|in) (?:the )?" + register,
    )
    value = plain(meaning)
    supported = any(re.fullmatch(r"(?:the )?" + pattern, value) for pattern in patterns)
    qualified = not supported and any(
        re.search(r"\b" + pattern + r"\b", value) for pattern in patterns
    )
    return supported, qualified


def _current_register_name_relation(
    mapping, documentation, available_fields, name_value_fields, name_group_value_fields
):
    """Bind a current-name companion to an explicitly documented register name.

    Only declared, native, value-producing fields participate. The compiler may
    supply those fields from structurally valid mappings of the same target.
    Each exact header still locates its own definition, not neighbouring semantics.
    A complete local relation takes precedence over the wider group scope.
    """
    local_fields = (
        set(name_value_fields)
        & set(mapping.get("source_fields", []))
        & set(available_fields)
    )
    group_fields = set(name_group_value_fields) & set(available_fields)
    for fields in (local_fields, local_fields | group_fields):
        bases, current, definitions = set(), set(), {}
        for field in sorted(fields):
            _, contexts = field_contexts(
                {**mapping, "source_field": field, "semantic_evidence": field},
                documentation,
                available_fields,
            )
            for context in contexts:
                meaning = plain(context.get("meaning", context["text"]))
                if _company_register_name_support(meaning)[0]:
                    bases.add(field)
                    definitions[field] = context
                if re.fullmatch(
                    r"(?:the )?current (?:company|corporation) name", meaning
                ):
                    current.add(field)
                    definitions[field] = context
        if len(bases) == len(current) == 1:
            base, new = next(iter(bases)), next(iter(current))
            if base != new:
                return {
                    "register_name_field": base,
                    "current_name_field": new,
                    "evidence_binding": "declared_value_fields_exact_native_headers",
                    "field_definitions": definitions,
                }
    return None


def registration_event_support(meaning):
    """Require a complete supported registration-event clause, not its prefix.

    A purpose/role/authority qualifier is not evidence of incorporation. Unknown
    prefixes and suffixes abstain rather than adding tax/charity/registry name blacklists.
    Independent later sentences may describe format; wrapped lines stay joined.
    """
    supported, qualified = False, False
    for clause in re.split(r"[.;]", meaning):
        value = plain(clause)
        for pattern in REGISTERED_DATE:
            for match in re.finditer(pattern, value):
                if value[match.end() :].strip() or value[
                    : match.start()
                ].strip() not in ("", "the"):
                    qualified = True
                else:
                    supported = True
    if re.search(
        r"\b(?:registered|incorporated|registration|incorporation) (?:or|and)\b",
        plain(meaning),
    ):
        qualified = True
    return supported and not qualified, qualified


LEGAL_TYPE = (r"\b(?:entity|legal entity) type\b", r"\blegal (?:form|structure|type)\b")
TYPE_LABELS = {
    "company": {
        "company",
        "corporation",
        "limited company",
        "private company",
        "public company",
        "proprietary company",
    },
    "sole_trader": {"sole trader", "sole proprietor"},
    "partnership": {"partnership", "limited partnership"},
    "trust": {"trust"},
    "incorporated_association": {"incorporated association"},
    "government": {
        "government",
        "government entity",
        "government body",
        "government department",
        "federal government department",
        "national government department",
        "state government department",
        "territory government department",
        "local government department",
        "central government department",
        "commonwealth government department",
    },
    "other": {"other"},
    "unknown": {"unknown"},
}


def _legal_form_label_supported(value, labels):
    if plain(value) in labels:
        return True
    if "government" in labels and re.fullmatch(
        r"(?:(?:federal|national|state|territory|local|central|commonwealth) )?"
        r"government authority",
        plain(value),
    ):
        # A complete authority category identifies the government body itself;
        # ownership, counterparties and qualified/alternative forms do not.
        return True
    if "company" in labels:
        # Geographic/legal modifiers do not change the explicit legal form.
        # A finite demonym grammar avoids accepting arbitrary prefixes such as
        # potential/former/unverified or company-related roles and providers.
        geography = r"(?:[a-z]+(?:ian|ean|ese|ish)|foreign|domestic|overseas)"
        company = (
            r"(?:"
            + geography
            + r" )?(?:(?:public|private|proprietary|limited) )?company"
        )
        if re.fullmatch(company, plain(value)):
            return True
        explanation = re.fullmatch(
            r"\s*(.*?)\s*\(\s*a company incorporated outside ([A-Z][A-Za-z -]{1,60}) but registered as a foreign company in \2\s*\)\s*",
            value,
        )
        if explanation and re.fullmatch(company, plain(explanation.group(1))):
            return True
    # One redundant parenthesis, with both complete labels independently in the
    # same canonical category. No nesting, alternatives or unknown qualifiers.
    match = re.fullmatch(
        r"\s*([A-Za-z][A-Za-z \t-]*?)\s*\(\s*([A-Za-z][A-Za-z \t-]*?)\s*\)\s*", value
    )
    return bool(match) and all(plain(label) in labels for label in match.groups())


ENTITY_STATUS = (
    r"\b(?:entity|company|corporation) (?:registration )?status\b",
    r"\b(?:registration )?status of (?:the |an? )?(?:entity|company|corporation)\b",
)
STATUS_LABELS = {
    "active": {"active"},
    "deregistered": {"deregistered", "de registered"},
    "in_liquidation": {"in liquidation"},
    "suspended": {"suspended"},
    "unknown": {"unknown"},
}


def _enum_relation_supported(raw, labels, contexts, *, legal_forms=False):
    # Only complete explicit relations in this field's description or separately
    # bound parameter cell qualify. Never match a label prefix of an ambiguous
    # definition, or search the whole table span (which also contains type cells).
    operator = r"(?:\s*(?:=|:|[-–—])\s*|\s+(?:means|denotes|indicates)\s+)"
    relation = re.compile(
        r"^[`\"']?" + re.escape(raw) + r"[`\"']?" + operator + r"(?:an?\s+)?(.*)$", re.I
    )
    next_relation = re.compile(
        r"^[`\"']?\w+[`\"']?(?:\s*[=:]|\s+(?:[-–—]|means|denotes|indicates)(?:\s|$))",
        re.I,
    )
    meanings = []
    for context in contexts:
        if context["kind"] != "document_field_definition":
            continue
        for text in (context.get("meaning", ""), context.get("enum_parameters", "")):
            current = None
            # Markdown table cells retain list line breaks as HTML br tags.
            # Split only this already field-bound text; original evidence and
            # complete qualifiers remain intact for quotation/meaning guards.
            for clause in re.split(r"[.;\n]|<br\s*/?>", text, flags=re.I):
                clause = clause.strip()
                if not clause:
                    continue
                match = relation.fullmatch(clause)
                if match:
                    meanings.append(match.group(1))
                    current = len(meanings) - 1
                elif next_relation.match(clause):
                    current = None
                elif current is not None:
                    # A wrapped qualifier still belongs to the preceding code;
                    # "in liquidation / or receivership" must not be narrowed.
                    meanings[current] += " " + clause
    return bool(meanings) and all(
        _legal_form_label_supported(meaning, labels)
        if legal_forms
        else plain(meaning) in labels
        for meaning in meanings
    )


def industry_support(mapping, documentation, available_fields=()):
    """Require an entity industry assertion; identifiers do not change its subject."""
    primary, contexts = field_contexts(mapping, documentation, available_fields)
    subject = r"(?:entity|business|company|corporation|firm)"
    industry = r"industry(?: classification)?(?: code)?"
    patterns = (
        r"\b" + subject + r"(?: s)? (?:primary )?" + industry + r"\b",
        r"\b" + industry + r" (?:of|for) (?:the |an? )?" + subject + r"\b",
    )
    # Product/service taxonomies and procurement attributes describe the traded
    # item or transaction even when the same row contains a business identifier.
    unrelated = r"\b(?:contract|contracts|procurement|product|products|commodity|commodities|goods|service|services|unspsc)\b"
    ambiguity = r"\b(?:not|unconfirmed|unverified|uncertain|may|might|could|possibly|either|or)\b"
    selected, contradicted = [], bool(re.search(unrelated, plain(primary)))
    for context in contexts:
        value = plain(context.get("meaning", context["text"]))
        if re.search(unrelated, value) or re.search(ambiguity, value):
            contradicted = True
        if _matches(value, patterns):
            selected.append(context)
    supported = bool(selected) and not contradicted
    return {
        "target": "entity.industry_code",
        "source_field": primary,
        "supported": supported,
        "basis": "same_field_entity_industry_subject",
        "contexts": selected,
        "reason_code": "entity_industry_supported"
        if supported
        else "non_entity_industry_or_ambiguous"
        if contradicted
        else "entity_industry_evidence_missing",
        "reason": (
            "Same-field evidence explicitly identifies the business/entity industry."
            if supported
            else "Industry code needs an unambiguous same-field business/entity industry definition. "
            "Contract, procurement and product/service classifications, a code scheme alone, or an ABN in the same row do not establish entity industry; "
            "remove the unsupported mapping and retain its source field unmapped in a complete revised config."
        ),
        "limitation": "Finite source assertions only; ANZSIC is preferred where available, not required and not independently verified.",
    }


def _object_status(value):
    # A company's permit remains a permit. A positive entity assertion can
    # explicitly contrast "not permit status" without becoming a contradiction.
    objects = r"(?:business name|trading name|licence|license|permit|contract|application|payment|charity|tax)"
    pattern = (
        r"\b(?:"
        + objects
        + r" (?:registration )?status|status of (?:the |an? )?(?:(?:company|entity|corporation)(?: s)? )?"
        + objects
        + r")\b"
    )
    for match in re.finditer(pattern, value):
        if not re.search(r"\bnot(?: the| a| an)? $", value[: match.start()]):
            return True
    return False


def timezone_support(source_field, evidence, documentation, available_fields=()):
    """Finite same-field UTC assertions, never a timezone guess from examples."""

    def normalize(value):
        # Split acronym-to-word boundaries locally (UTCTimestamp); do not
        # change the established normalization of other semantic checks.
        return plain(re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value))

    result = {
        "source_field": source_field,
        "supported": False,
        "contexts": [],
        "basis": "finite_same_field_explicit_utc_assertion",
        "reason_code": "utc_source_evidence_missing_or_ambiguous",
        "reason": "Explicit timezone UTC requires an unambiguous UTC assertion for this exact timestamp field, "
        "not an unrelated field, format example, dataset or ingest proxy. Omit unsupported optional observed_time in a new proposal.",
        "limitation": "Finite documentary grammar only; source assertions are not independent truth.",
    }
    if (
        not isinstance(source_field, str)
        or source_field not in available_fields
        or not isinstance(evidence, str)
        or not evidence
        or not (evidence == source_field or evidence in documentation)
    ):
        return result
    _, contexts = field_contexts(
        {
            "source_field": source_field,
            "source_fields": [source_field],
            "semantic_evidence": evidence,
        },
        documentation,
        available_fields,
    )
    # Conservatively veto the entire bound definition on these signals. Even a
    # positive clause cannot override an example, ambiguity or different clock.
    excluded = (
        r"\b(?:not|no|non|never|neither|cannot|without|unknown|unspecified|unconfirmed|unverified|uncertain|may|might|could|possibly|"
        r"sometimes|usually|typically|either|or|mixed|unless|except|if|should|would|can|local|offset|excluding|"
        r"assume|assumed|default|example|examples|sample|samples|format|placeholder|dataset|catalogue|catalog|"
        r"ingest|ingested|ingestion|retrieval|retrieved|crawl|crawled|snapshot|fetch|fetched|download|downloaded|proxy|proxies)\b"
        r"|\b(?:isn|aren|wasn|weren|don|doesn|didn|couldn|shouldn|wouldn|mustn|won|hasn|haven|hadn) t\b"
    )
    temporal = r"(?:time|times|timestamp|timestamps|datetime|datetimes|date|dates|observed|recorded)"
    header_time = (
        r"(?:(?:source|record|company|entity|observation|registration|registered|creation|created|update|updated|last) ){0,3}"
        + temporal
        + r"(?: time)?"
    )
    asserted = (
        r"(?:(?:all|the|these|this) )?(?:(?:source stated|record stated|observation|observed|recorded|source|record) )?"
        r"(?:time|times|timestamp|timestamps|datetime|datetimes|date time|date times|values?|timezone|time zone)"
        r"(?: (?:in|of|for) (?:this|the) (?:field|column))?"
        r"(?: (?:is|are))?(?: (?:expressed|stored|recorded|reported|provided))?(?: (?:in|as))? utc"
    )
    contradicted = bool(
        re.search(excluded, normalize(source_field))
        or re.search(r"\butc\s*[+-]", source_field, re.I)
    )
    for context in contexts:
        meaning = context.get("meaning", context["text"])
        normalized = normalize(meaning)
        if (
            re.search(excluded, normalized)
            or re.search(r"\butc\s*[+-]", meaning, re.I)
            or re.search(r"\b(?:gmt|aest|aedt|est|pst|bst)\b", normalized)
        ):
            contradicted = True
            continue
        if context["kind"] == "header":
            supported = bool(
                re.fullmatch(header_time + r" utc|utc " + header_time, normalized)
            )
        else:
            # Clauses are matched in full; a stray UTC token or ISO example
            # cannot satisfy an assertion. Original exact spans remain attached.
            clauses = [
                normalize(clause)
                for clause in re.split(r"[.;\n]", meaning)
                if clause.strip()
            ]
            supported = any(
                re.fullmatch(asserted, clause)
                or re.fullmatch(r"utc (?:" + temporal + r")", clause)
                for clause in clauses
            )
        if supported:
            result["contexts"].append(context)
    result["supported"] = bool(result["contexts"]) and not contradicted
    if result["supported"]:
        result["reason"] = (
            "An exact native header or bound original field definition explicitly states UTC for this timestamp field."
        )
    return result


def observation_validity_support(source_field, documentation, available_fields, kind):
    """Require explicit whole-record validity before applying a date to every claim."""
    result = {
        "source_field": source_field,
        "supported": False,
        "contexts": [],
        "basis": "same_field_whole_record_validity_definition",
        "reason": "Global validity needs a complete same-field definition applying to the record, all observations or all claims; name/permit lifetimes and generic dates are insufficient.",
    }
    if source_field not in available_fields or kind not in {"valid_from", "valid_to"}:
        return result
    edge, relation = ("start", "from") if kind == "valid_from" else ("end", "until")
    owner = r"(?:(?:the |this )?record|all (?:observations|claims)(?: in (?:the|this) record)?)"
    patterns = (
        r"(?:validity "
        + edge
        + r"(?: date| time| timestamp)?|"
        + edge
        + r" (?:date|time|timestamp) of (?:the )?validity(?: period)?) (?:for|of) "
        + owner,
        r"(?:date|time|timestamp) "
        + relation
        + r" which "
        + owner
        + r" (?:is|are) valid",
    )
    _, contexts = field_contexts(
        {
            "source_field": source_field,
            "source_fields": [source_field],
            "semantic_evidence": source_field,
        },
        documentation,
        available_fields,
    )
    contradicted = False
    for context in contexts:
        if context["kind"] != "document_field_definition":
            continue
        if any(
            re.fullmatch(pattern, plain(context["meaning"])) for pattern in patterns
        ):
            result["contexts"].append(context)
        else:
            contradicted = True
    result["supported"] = bool(result["contexts"]) and not contradicted
    if result["supported"]:
        result["reason"] = (
            "This field explicitly defines the validity boundary of the record or every observation/claim."
        )
    return result


def identifier_support(mapping, documentation, kind, available_fields=()):
    primary, contexts = field_contexts(mapping, documentation, available_fields)
    patterns = {
        "abn": (r"\babn\b", r"\baustralian business number\b"),
        "acn": (r"\bacn\b", r"\baustralian company number\b"),
    }
    selected, contradicted = [], False
    for context in contexts:
        text = plain(context.get("meaning", context["text"]))
        other = "acn" if kind == "abn" else "abn"
        if _matches(text, patterns[other]) or re.search(
            r"\barbn\b|\baustralian registered body number\b", text
        ):
            contradicted = True
        if re.search(r"\b(?:unconfirmed|unverified|may|might|possibly)\b", text) or any(
            re.search(r"\bnot(?: the| an?)? " + pattern, text)
            for pattern in patterns[kind]
        ):
            contradicted = True
        if _matches(text, patterns[kind]):
            selected.append(context)
    return {
        "target": "entity." + kind,
        "source_field": primary,
        "supported": bool(selected) and not contradicted,
        "basis": "same_field_identifier_type_definition",
        "contexts": selected,
    }


def labelled_identifier_support(mapping, documentation, kind, available_fields=()):
    """Bind a label-checked value to its header or its own identifier definition.

    A source-level identifier mention cannot locate a native field. Documentary
    quotes retain the existing field/union meaning checks; the whole-cell label
    and checksum are still mandatory at execution, including for these quotes.
    """
    primary, contexts = field_contexts(mapping, documentation, available_fields)
    evidence = mapping.get("semantic_evidence", "")
    result = {
        "target": "entity." + kind,
        "source_field": primary,
        "supported": bool(primary) and evidence == primary,
        "basis": "matching_whole_cell_label_and_checksum",
        "evidence_binding": "exact_primary_native_header",
        "contexts": [],
    }
    if result["supported"]:
        return result

    # field_contexts preserves a complete original span but trims neighboring
    # definitions from its meaning. Do not accept a quote that only occurs in
    # the neighboring portion of that span. Whitespace joining accommodates
    # original wrapped definitions, never fabricated/rewritten quotations.
    quote = " ".join(evidence.split()) if isinstance(evidence, str) else ""
    quoted_contexts = [
        context
        for context in contexts
        if context["kind"] == "document_field_definition"
        and evidence in documentation
        and quote
        and (
            quote in " ".join(context["meaning"].split()) or evidence == context["text"]
        )
    ]
    for support in (
        identifier_support(mapping, documentation, kind, available_fields),
        mixed_identifier_support(mapping, documentation, available_fields),
    ):
        selected = [
            context for context in support["contexts"] if context in quoted_contexts
        ]
        if support["supported"] and selected:
            result.update(
                supported=True,
                evidence_binding="exact_same_field_identifier_definition",
                contexts=selected,
            )
            return result
    result.update(
        evidence_binding="unresolved",
        reason_code="labelled_identifier_evidence_not_bound",
    )
    return result


def mixed_identifier_support(mapping, documentation, available_fields=()):
    """Authorize a closed source-declared union, never type inference from digits."""
    primary, contexts = field_contexts(mapping, documentation, available_fields)
    abn = r"(?:australian business number(?: abn)?|abn)"
    acn = r"(?:australian company number(?: acn)?|acn)"
    union = r"(?:" + abn + r" or " + acn + "|" + acn + r" or " + abn + r")"
    # Match the complete source assertion, not a substring in an example or a
    # larger list of identifier types. Finite subject words bind the union to
    # the row's entity; unrelated parent/supplier identifiers cannot borrow it.
    owner = (
        r"(?:the )?(?:(?:registered|current|principal|credit|financial) )?"
        r"(?:entity|business|company|organisation|organization|licensee|licence holder|license holder|applicant|registrant|operator)"
    )
    closed = (
        r"(?:(?:"
        + owner
        + r") s )?"
        + union
        + r"(?: number| identifier)?(?: (?:of|for|belonging to) "
        + owner
        + r")?"
    )
    selected, contradicted = [], False
    for context in contexts:
        text = plain(context.get("meaning", context["text"]))
        matched = re.fullmatch(closed, text)
        if (
            re.search(
                r"\b(?:abn|acn|australian business number|australian company number)\b",
                text,
            )
            and not matched
        ):
            # A type-bearing header such as ABN_ACN may locate a field's
            # explicit definition, but does not itself claim an open union.
            if context["kind"] != "header" or re.search(
                r"\b(?:or|and|example|sample|format|arbn|nzbn|afsl|tfn)\b", text
            ):
                contradicted = True
        if re.search(
            r"\b(?:unconfirmed|unverified|may|might|possibly|not|and|arbn|nzbn|afsl|tfn|"
            r"example|examples|sample|illustrative|illustration|format|pattern|placeholder|dummy|"
            r"parent|supplier|buyer|purchaser|customer|subsidiary)\b",
            text,
        ):
            contradicted = True
        if matched:
            selected.append(context)
    return {
        "target": mapping["canonical_field"],
        "source_field": primary,
        "supported": bool(selected) and not contradicted,
        "basis": "same_field_explicit_abn_or_acn_union_and_disjoint_checked_value_grammar",
        "contexts": selected,
        "reason": "A closed same-field ABN or ACN definition and the complete value's type-specific checksum are both required; numeric length alone is insufficient.",
    }


def location_component_support(
    mapping, documentation, available_fields=(), component=None
):
    """Bind a finite location-tuple extraction to the same native location field."""
    primary, contexts = field_contexts(mapping, documentation, available_fields)
    selected, contradicted = [], False
    for context in contexts:
        text = plain(context.get("meaning", context["text"]))
        if re.search(
            r"\b(?:unconfirmed|unverified|may|might|possibly|not|either|partial|truncated|historical|previous|former)\b|"
            r"\baddress line\b|\bstreet address\b|\bstreet only\b",
            text,
        ):
            contradicted = True
        # A named location/address field establishes the source's geographic
        # subject. The operator still requires the whole explicit three-part
        # tuple; it never uses an incidental postcode found in arbitrary prose.
        if re.search(r"\b(?:location|address)\b", text) or all(
            re.search(pattern, text)
            for pattern in (
                r"\b(?:locality|suburb|city|town)\b",
                r"\bstate\b",
                r"\b(?:postcode|postal code)\b",
            )
        ):
            selected.append(context)
    return {
        "target": "address." + str(component),
        "source_field": primary,
        "supported": bool(selected) and not contradicted,
        "basis": "same_field_location_subject_and_whole_explicit_au_location_tuple",
        "contexts": selected,
        "reason": "AU location components require same-field location/address evidence and a complete locality, explicit Australian state, four-digit postcode tuple. No state inference, street truncation or unrelated-field evidence is allowed.",
        "limitation": "Structural source extraction does not verify that a locality/postcode exists or that its stated state is geographically correct.",
    }


def full_address_join_support(mapping, documentation, available_fields=()):
    """A geographic-only tuple cannot acquire street-address meaning by joining.

    Bind each preserving value branch to its own native field. A predicate-only
    street read, an enum replacement or a locality fallback is not street content.
    """
    primary = mapping.get("source_field")
    header_pattern = (
        r"\bstreet(?: address| name)?$|\baddress line(?: ?[1-9]| one| two| three)?$|"
        r"\bpremises(?: address)?$|\b(?:full|complete|whole|business|registered|postal)"
        r"(?: office| business| registered| postal)? address$"
    )
    owner = (
        r"(?:the )?(?:(?:current|principal|registered|postal|business|credit|financial) ){0,3}"
        r"(?:entity|company|business|organisation|organization|licensee|licence holder|license holder|applicant|registrant|operator)"
    )
    definition_pattern = (
        r"(?:the )?(?:(?:first|second|third|principal|registered|postal|business|full|complete|whole) )?"
        r"(?:street(?: address| name)?(?: line(?: ?[1-9]| one| two| three)?)?|"
        r"address line(?: ?[1-9]| one| two| three)?|premises(?: address)?|"
        r"(?:full|complete|whole|business|registered|postal)(?: office)? address)"
        r"(?: (?:of|for) " + owner + r")?"
    )
    bindings = {}

    def field_support(name):
        if name in bindings:
            return bindings[name]
        value = {
            "source_field": name,
            "source_fields": [name],
            "semantic_evidence": name,
        }
        _, contexts = field_contexts(value, documentation, available_fields)
        selected, contradicted = [], False
        for context in contexts:
            text = plain(context.get("meaning", context["text"]))
            positive = bool(
                re.search(header_pattern, text)
                if context["kind"] == "header"
                else re.fullmatch(definition_pattern, text)
            )
            if (
                (context["kind"] == "document_field_definition" and not positive)
                or re.search(
                    r"\b(?:not|unconfirmed|unverified|may|might|possibly|truncated|incomplete)\b",
                    text,
                )
                or (
                    not positive
                    and re.search(
                        r"\b(?:locality|suburb|city|town|state|postcode|postal code|country)\b",
                        text,
                    )
                )
            ):
                contradicted = True
            if positive:
                selected.append(context)
        result = {
            "source_field": name,
            "supported": bool(selected) and not contradicted,
            "evidence_binding": {
                "kind": "declared_value_field_exact_native_header",
                "header": name,
            },
            "contexts": selected,
        }
        bindings[name] = result
        return result

    def preserving(node):
        op = node["op"]
        if op == "field":
            return True, field_support(node["name"])["supported"]
        if op == "omit":
            return False, True
        if op in {"trim", "collapse_ws"}:
            return preserving(node["input"])
        if op == "when":
            branches = [preserving(node[key]) for key in ("then", "else")]
            return any(value[0] for value in branches), all(
                value[1] for value in branches
            )
        return True, False

    components = []
    for index, node in enumerate(mapping["ast"]["inputs"]):
        can_emit, supported = preserving(node)
        components.append(
            {
                "pointer": "/inputs/" + str(index),
                "can_emit": can_emit,
                "street_bearing_when_present": supported and can_emit,
            }
        )
    fields = mapping.get("source_fields", [])
    has_location_context = bool(
        re.search(
            r"\b(?:suburb|locality|city|town|state|postcode|postal|country|full|complete)\b",
            plain(" ".join(fields) + " " + mapping.get("semantic_evidence", "")),
        )
    )
    return {
        "target": "address.full",
        "source_field": primary,
        "supported": len(set(fields)) >= 2
        and has_location_context
        and any(value["street_bearing_when_present"] for value in components),
        "basis": "joined_address_requires_preserved_same_field_street_or_whole_address_component",
        "components": components,
        "field_bindings": list(bindings.values()),
        "reason": "An address join needs multiple fields with location context and an actual value-producing street/address-line/premises or whole-address component, supported by that field's own exact native header/definition and preserved on every non-null branch. Street-lines-only and locality/state/postcode/country-only joins remain component information; omit address.full and preserve supported component mappings.",
        "limitation": "Required street-bearing source content prevents geographic-only fabrication; it does not independently verify every real-world address or prove complete delivery details.",
    }


def _matches(text, patterns):
    value = plain(text)
    return any(re.search(pattern, value) for pattern in patterns)


def address_support(mapping, documentation, inventory=()):
    """Preserve a source's address field, without certifying postal completeness."""
    primary, contexts = field_contexts(mapping, documentation, inventory)
    result = {
        "target": "address.full",
        "source_field": primary,
        "supported": False,
        "basis": "same_field_whole_address_preserving_transform",
        "contexts": [],
        "reason": "No unambiguous same-field address assertion is bound to this mapping.",
        "limitation": "A source assertion and a preserving transform do not independently verify address completeness.",
    }
    evidence = mapping.get("semantic_evidence", "")
    if (
        not isinstance(evidence, str)
        or not evidence
        or not (evidence in documentation or evidence in inventory)
    ):
        result.update(
            reason_code="address_exact_quote_missing",
            reason="Address evidence must be an exact native header or contiguous source-document quote.",
        )
        return result
    ast = mapping.get("ast", {})
    # The existing compiler enforces shape and dependency equality; repeat this
    # narrow chain check so support cannot authorize token removal or assembly.
    depth = 0
    while (
        isinstance(ast, dict)
        and ast.get("op") in {"trim", "collapse_ws"}
        and depth < 32
    ):
        if set(ast) != {"op", "input"}:
            break
        ast = ast["input"]
        depth += 1
    if mapping.get("source_fields") != [primary] or ast != {
        "op": "field",
        "name": primary,
    }:
        result.update(
            reason_code="address_transform_not_whole_field",
            reason="Whole-address support requires one source field with only optional trim/collapse_ws operations.",
        )
        return result
    # `full` denotes the source's whole address value, not verified delivery
    # completeness. An address property need not claim 'full' in its heading.
    # Subject ownership is checked separately for both execution backends.
    patterns = (r"\baddress\b",)
    components = r"locality|suburb|city|town|state|postcode|postal code|country|latitude|longitude|coordinates"
    partial = (
        r"\b(?:street|line)(?: address)? only\b|\bonly (?:the )?(?:street|first|second|address line)\b|"
        r"\bstreet (?:address|lines?(?: ?[0-9]+)?)\b|\baddress lines?(?: ?[0-9]+)?\b|"
        r"\b(?:first|second|third) (?:address )?line\b|\b(?:partial|incomplete|truncated) (?:business )?address\b|"
        r"\b(?:email|e mail|ip|internet|web|mac|wallet) address\b|"
        r"\baddress (?:id|identifier|reference|code|status|count|type|date|flag|verification|validity|"
        + components
        + r")\b"
    )
    ambiguity = r"\b(?:not|unconfirmed|unverified|uncertain|may|maybe|might|could|possibly|either|or|without|lacks|excluding)\b"
    contradicted = bool(
        re.search(partial, plain(primary)) or re.search(ambiguity, plain(primary))
    )
    for context in contexts:
        value = plain(context.get("meaning", context["text"]))
        if re.search(partial, value) or re.search(ambiguity, value):
            contradicted = True
        if context["kind"] == "document_field_definition":
            # A component definition can mention the address it belongs to;
            # that mention must not promote the component into a whole value.
            component_definition = re.search(
                r"^(?:the |a |an )?(?:(?:company|business|entity|postal|address) )*"
                r"(?:" + components + r")\b",
                value,
            )
            if component_definition or (
                not _matches(value, patterns)
                and re.search(r"\b(?:" + components + r")\b", value)
            ):
                contradicted = True
        if _matches(value, patterns):
            result["contexts"].append(context)
    result["supported"] = bool(result["contexts"]) and not contradicted
    if result["supported"]:
        result["reason"] = (
            "Same-field evidence identifies an address and the transformation preserves every substantive raw token; postal completeness is not asserted."
        )
    else:
        result["reason_code"] = (
            "address_evidence_partial_or_ambiguous"
            if contradicted
            else "address_same_field_evidence_missing"
        )
    return result


def full_address_support(mapping, documentation, inventory=()):
    """Check each preserving output branch; predicates cannot supply address content."""

    def walk(node, pointer=""):
        op = node["op"]
        if op == "omit":
            return False, {"pointer": pointer, "supported": True, "can_emit": False}
        if op in {"trim", "collapse_ws"}:
            return walk(node["input"], pointer + "/input")
        if op == "when":
            branches = [
                walk(node[key], pointer + "/" + key) for key in ("then", "else")
            ]
            can_emit = any(value[0] for value in branches)
            failed = next(
                (support for _, support in branches if not support["supported"]), None
            )
            basis = failed or next(
                (support for emits, support in branches if emits), branches[0][1]
            )
            return can_emit, {
                **basis,
                "supported": failed is None,
                "reason": basis.get("reason", "This address branch always omits its value."),
                "address_branch_support": [support for _, support in branches],
            }
        if op in {"field", "join_address"}:
            fields = sorted(_value_fields(node))
            primary = mapping.get("source_field")
            if primary not in fields:
                primary = fields[0] if fields else ""
            bound = {
                **mapping,
                "source_field": primary,
                "source_fields": fields,
                "semantic_evidence": primary,
                "ast": node,
            }
            support = (
                address_support(bound, documentation, inventory)
                if op == "field"
                else full_address_join_support(bound, documentation, inventory)
            )
            return True, {
                **support,
                "pointer": pointer,
                "evidence_binding": {
                    "kind": "declared_value_fields_exact_native_headers",
                    "headers": fields,
                },
            }
        return True, {
            "target": "address.full",
            "source_field": mapping.get("source_field"),
            "supported": False,
            "pointer": pointer,
            "reason_code": "address_transform_not_whole_field",
            "reason": "Address output paths must preserve a whole source address or a supported address join; only trim/collapse_ws and conditional omission may wrap them.",
        }

    can_emit, support = walk(mapping["ast"])
    if not can_emit:
        return {
            **support,
            "target": "address.full",
            "source_field": mapping.get("source_field"),
            "supported": False,
            "reason_code": "address_mapping_has_no_value",
            "reason": "Address mapping has no value-producing branch.",
        }
    return support


def _name_branch_support(
    mapping,
    documentation,
    available_fields,
    result,
    name_value_fields,
    name_group_value_fields,
):
    # The primary field's assertion cannot authorize another name returned by
    # a conditional branch. Predicate-only fields do not supply name values.
    # Exact native headers locate each actual output field's own definition.
    # They are disclosed as runtime bindings, not model-authored quotations.
    from .engine import ast_nodes

    checked, branches = {result["source_field"]}, []
    for pointer, node in ast_nodes(mapping["ast"]):
        if (
            node["op"] != "field"
            or "/condition/" in pointer + "/"
            or node["name"] in checked
        ):
            continue
        checked.add(node["name"])
        if (
            node["name"] not in mapping.get("source_fields", [])
            or node["name"] not in available_fields
        ):
            support = {
                "source_field": node["name"],
                "supported": False,
                "reason": "An output name field must be declared and present in the native inventory.",
            }
        else:
            alternate = {
                **mapping,
                "source_field": node["name"],
                "ast": node,
                "semantic_evidence": node["name"],
            }
            support = check_support(
                alternate,
                documentation,
                available_fields,
                name_value_fields=name_value_fields,
                name_group_value_fields=name_group_value_fields,
            )
            support["evidence_binding"] = {
                "kind": "declared_value_field_exact_native_header",
                "header": node["name"],
            }
        branches.append({"pointer": pointer, **support})
    if not branches:
        return result
    result = {**result, "name_branch_support": branches}
    failed = next((branch for branch in branches if not branch["supported"]), None)
    if failed is not None:
        result.update(
            supported=False,
            reason_code="name_branch_evidence_missing_or_ambiguous",
            unsupported_name_branch={
                "source_field": failed["source_field"],
                "pointer": failed["pointer"],
            },
            reason="Every value-producing name branch needs its own native-header-bound source definition; one supported primary name cannot authorize an unsupported alternate.",
        )
    return result


def _enum_value_support(mapping, documentation, available_fields, result):
    """Bind enum roles to actual value fields, independently of predicate evidence."""
    from .engine import ast_nodes

    bindings = {}
    for pointer, node in ast_nodes(mapping["ast"]):
        if node["op"] != "field" or "/condition/" in pointer + "/":
            continue
        name = node["name"]
        if name in bindings:
            continue
        support = result
        if name != result["source_field"]:
            support = check_support(
                {
                    **mapping,
                    "source_field": name,
                    "semantic_evidence": name,
                    "ast": node,
                },
                documentation,
                available_fields,
            )
        bindings[name] = {
            **support,
            "pointer": pointer,
            "evidence_binding": {
                "kind": "declared_value_field_exact_native_header",
                "header": name,
            },
        }
    return bindings


def name_role_reviewable(support):
    """Only an unresolved name-role check can use independent model review.

    This does not decide semantics: it preserves recognized contradictions and
    leaves subject, quotation, dependency and execution checks to their owners.
    """
    return bool(
        support
        and support.get("target") in {"entity.legal_name", "entity.trading_name"}
        and support.get("basis") == "finite_field_bound_header_or_document_definition"
        and not support.get("supported")
        and not support.get("name_role_conflict")
        and all(
            branch.get("supported") or name_role_reviewable(branch)
            for branch in support.get("name_branch_support", [])
        )
    )


def check_support(
    mapping,
    documentation,
    available_fields=(),
    *,
    on_unsupported_conversion=None,
    name_value_fields=None,
    name_group_value_fields=(),
):
    """Return an inspectable finite check; the caller retains strict quote/DSL checks."""
    target = mapping["canonical_field"]
    if name_value_fields is None and target in {
        "entity.legal_name",
        "entity.trading_name",
    }:
        from .engine import ast_nodes

        name_value_fields = {
            node["name"]
            for pointer, node in ast_nodes(mapping["ast"])
            if node["op"] == "field" and "/condition/" not in pointer + "/"
        }
    if target == "entity.industry_code":
        return industry_support(mapping, documentation, available_fields)
    if target not in {
        "entity.legal_name",
        "entity.trading_name",
        "entity.date_registered",
        "entity.entity_type",
        "entity.status",
    }:
        return None
    if (
        target in {"entity.entity_type", "entity.status"}
        and mapping.get("claim_kind") == "unknown_abstention"
    ):
        return {
            "target": target,
            "supported": True,
            "basis": "declared_unknown_abstention_not_informative_inference",
        }
    primary, contexts = field_contexts(mapping, documentation, available_fields)
    patterns = {
        "entity.legal_name": LEGAL,
        "entity.trading_name": TRADING,
        "entity.date_registered": REGISTERED_DATE,
        "entity.entity_type": LEGAL_TYPE,
        "entity.status": ENTITY_STATUS,
    }[target]
    selected = []
    contradicted = False
    name_role_conflict = False
    object_status = False
    for context in contexts:
        meaning = context.get("meaning", context["text"])
        value = plain(meaning)
        negated = any(
            re.search(r"\bnot(?: (?:the|a|an|necessarily))? " + pattern, value)
            for pattern in patterns
        )
        if target in {"entity.legal_name", "entity.trading_name"} and negated:
            name_role_conflict = True
        if target == "entity.legal_name" and re.search(
            r"\bregistered (?:business|trading) name\b", value
        ):
            name_role_conflict = True
        if negated or re.search(
            r"\b(?:unconfirmed|unverified|may|might|possibly)\b", value
        ):
            # Conservative ambiguity handling; not a claim to understand all negation.
            contradicted = True
            continue
        if target == "entity.legal_name" and (
            "person" in plain(primary).split()
            or re.search(
                r"\bregistered (?:business|trading) name\b|\b(?:person|individual|director|contact|representative)(?: s)? (?:legal )?name\b|\b(?:legal )?name of (?:the |an? )?(?:person|individual|director|contact|representative)\b",
                value,
            )
        ):
            contradicted = True
            continue
        if target == "entity.legal_name":
            register_name, qualified_name = _company_register_name_support(meaning)
            if qualified_name:
                contradicted = True
                continue
            if register_name:
                selected.append(context)
                continue
            if re.fullmatch(r"(?:the )?current (?:company|corporation) name", value):
                relation = _current_register_name_relation(
                    mapping,
                    documentation,
                    available_fields,
                    name_value_fields,
                    name_group_value_fields,
                )
                if relation and relation["current_name_field"] == primary:
                    selected.append({**context, "register_name_relation": relation})
                    continue
        if target == "entity.date_registered":
            date_supported, qualified_event = registration_event_support(meaning)
            if qualified_event or re.search(
                r"\b(?:licence|license|permit|contract|name)\b", value
            ):
                contradicted = True
                continue
            if date_supported:
                selected.append(context)
            continue
        if target == "entity.entity_type" and re.search(
            r"\b(?:charity status|person name|tax status)\b", value
        ):
            contradicted = True
            continue
        if target == "entity.entity_type" and re.fullmatch(
            r"(?:(?:specifies|describes|indicates) (?:the )?)?(?:(?:company|corporation) type|type of (?:the |a )?(?:company|corporation))",
            plain(re.split(r"[.;]", meaning, maxsplit=1)[0]),
        ):
            selected.append(context)
            continue
        if target == "entity.status" and _object_status(value):
            object_status = True
            contradicted = True
            continue
        if _matches(meaning, patterns):
            selected.append(context)
    result = {
        "target": target,
        "source_field": primary,
        "supported": bool(selected) and not contradicted,
        "basis": "finite_field_bound_header_or_document_definition",
        "contexts": selected,
        "limitation": "Recognized source assertions are not independent truth or complete language understanding.",
    }
    if target in {"entity.legal_name", "entity.trading_name"}:
        result["name_role_conflict"] = name_role_conflict
    if not result["supported"]:
        if target == "entity.status":
            result["reason_code"] = (
                "object_status_not_entity"
                if object_status
                else "entity_status_evidence_missing_or_ambiguous"
            )
        if target == "entity.date_registered":
            result["reason_code"] = (
                "registration_event_not_company_or_ambiguous"
                if contradicted
                else "registration_event_evidence_missing"
            )
        result["reason"] = (
            (
                "Object status does not establish entity status. "
                if object_status
                else "Generic status lacks subject evidence. "
                if target == "entity.status"
                else ""
            )
            + "No unambiguous same-field source evidence establishes "
            + target
            + "; retain unsupported fields unmapped. Plain names, unrelated registered/legal words and object registration dates are insufficient."
        )
        if target in {"entity.legal_name", "entity.trading_name"}:
            return _name_branch_support(
                mapping,
                documentation,
                available_fields,
                result,
                name_value_fields,
                name_group_value_fields,
            )
        return result
    if target in {"entity.legal_name", "entity.trading_name"}:
        return _name_branch_support(
            mapping,
            documentation,
            available_fields,
            result,
            name_value_fields,
            name_group_value_fields,
        )
    if target in {"entity.entity_type", "entity.status"}:
        from .engine import ast_nodes

        bindings = _enum_value_support(mapping, documentation, available_fields, result)
        failed = next(
            (binding for binding in bindings.values() if not binding["supported"]), None
        )
        if failed is not None:
            return {
                **result,
                "supported": False,
                "reason_code": "enum_value_evidence_missing_or_ambiguous",
                "enum_value_support": list(bindings.values()),
                "reason": "Every value-producing enum field needs its own same-field meaning evidence; a primary or predicate field cannot authorize another field's values.",
            }
        # Each lookup checks its input field's code table, never another branch's.
        # Keep predicate conversion checks: an unsupported conversion can change
        # which output branch is selected and cannot be safely pruned.
        result = {**result, "enum_value_support": list(bindings.values())}
        first_failure, unsupported_count = None, 0
        for at, node in ast_nodes(mapping["ast"]):
            if node.get("op") != "enum_lookup":
                continue
            for name in sorted(_value_fields(node["input"])):
                binding = bindings.get(name)
                if binding is None:
                    binding = (
                        result
                        if name == primary
                        else check_support(
                            {
                                **mapping,
                                "source_field": name,
                                "semantic_evidence": name,
                                "ast": {"op": "field", "name": name},
                            },
                            documentation,
                            available_fields,
                        )
                    )
                for raw, canonical in sorted(node["values"].items()):
                    labels = (
                        TYPE_LABELS if target == "entity.entity_type" else STATUS_LABELS
                    ).get(canonical, set())
                    if binding["supported"] and (
                        target == "entity.entity_type"
                        and _legal_form_label_supported(raw, labels)
                        or target == "entity.status"
                        and plain(raw) in labels
                        or canonical == "unknown"
                    ):
                        continue
                    if (
                        binding["supported"]
                        and labels
                        and _enum_relation_supported(
                            raw,
                            labels,
                            binding["contexts"],
                            legal_forms=target == "entity.entity_type",
                        )
                    ):
                        continue
                    failure = {
                        **result,
                        "supported": False,
                        "reason": "Conversion needs same-field subject and meaning evidence for each raw label/code; an unexplained or ambiguous category does not establish "
                        + target
                        + ". Remove any unsupported conversion or mapping in a complete revised config, list unused source fields unmapped with reasons, and revalidate the entire proposal; no partial approval is inherited.",
                        "unsupported_conversion": {
                            "source_field": name,
                            "raw_label": raw,
                            "canonical_value": canonical,
                        },
                    }
                    unsupported_count += 1
                    if first_failure is None:
                        first_failure = failure
                    if on_unsupported_conversion is not None:
                        on_unsupported_conversion(
                            at + "/values/" + raw.replace("~", "~0").replace("/", "~1"),
                            failure,
                        )
        if first_failure is not None:
            return {**first_failure, "unsupported_conversion_count": unsupported_count}
    return result
