DEFAULT_PER_PAGE = 10


def get_page_from_request(default=1):
    from flask import request

    return max(1, request.args.get("page", default, type=int) or default)


def build_page_url(page, endpoint=None, **extra):
    from flask import request, url_for

    endpoint = endpoint or request.endpoint
    args = request.args.to_dict()
    args.update(extra)
    if page <= 1:
        args.pop("page", None)
    else:
        args["page"] = page
    return url_for(endpoint, **args)


def make_pagination(total, page, per_page=DEFAULT_PER_PAGE, endpoint=None, **url_kwargs):
    page = max(1, page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = min(page, total_pages)
    start = (page - 1) * per_page + 1 if total else 0
    end = min(page * per_page, total)

    window = 5
    start_page = max(1, page - window // 2)
    end_page = min(total_pages, start_page + window - 1)
    start_page = max(1, end_page - window + 1)
    pages = list(range(start_page, end_page + 1))

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": build_page_url(page - 1, endpoint, **url_kwargs) if page > 1 else None,
        "next_url": build_page_url(page + 1, endpoint, **url_kwargs) if page < total_pages else None,
        "page_urls": {p: build_page_url(p, endpoint, **url_kwargs) for p in pages},
        "pages": pages,
        "start": start,
        "end": end,
    }


def sql_page_clause(page, per_page=DEFAULT_PER_PAGE):
    page = max(1, page)
    offset = (page - 1) * per_page
    return " LIMIT %s OFFSET %s", [per_page, offset]


def paginate_items(items, page=None, per_page=DEFAULT_PER_PAGE, endpoint=None, page_param="page", **url_kwargs):
    """Slice a list and return (page_items, pagination_meta) for template macros."""
    from flask import has_request_context, request, url_for

    items = list(items or [])
    if page is None:
        if has_request_context():
            page = max(1, request.args.get(page_param, 1, type=int) or 1)
        else:
            page = 1

    total = len(items)

    def _build(page_num):
        if not has_request_context():
            return f"?{page_param}={page_num}" if page_num > 1 else "?"
        args = request.args.to_dict()
        args.update(url_kwargs)
        if page_num <= 1:
            args.pop(page_param, None)
        else:
            args[page_param] = page_num
        return url_for(endpoint or request.endpoint, **args)

    page = max(1, page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = min(page, total_pages)
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_items = items[start_idx:end_idx]

    window = 5
    start_page = max(1, page - window // 2)
    end_page = min(total_pages, start_page + window - 1)
    start_page = max(1, end_page - window + 1)
    pages = list(range(start_page, end_page + 1))

    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": _build(page - 1) if page > 1 else None,
        "next_url": _build(page + 1) if page < total_pages else None,
        "page_urls": {p: _build(p) for p in pages},
        "pages": pages,
        "start": start_idx + 1 if total else 0,
        "end": min(end_idx, total),
    }
    return page_items, pagination

