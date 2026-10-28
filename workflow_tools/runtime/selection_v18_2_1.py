_v1821_category_base = canonical_category_name


def canonical_category_name(value):
    # This is a specific family alias, not a substring/fuzzy category bypass.
    text = re.sub(r'\s+', '', _v1821_unicode.normalize('NFKC', str(value or '')))
    if text in ('后备保护器', '后备保护器类'):
        return 'SPD后备保护器'
    return _v1821_category_base(value)


_v1821_candidate_base = candidate_check


def candidate_check(req, rec):
    failures, warnings, score = _v1821_candidate_base(req, rec)
    error = v1821_pole_error((req.get('requirements') or {}).get('pole_count'), rec)
    if error:
        failures.append(error)
    return failures, warnings, score
