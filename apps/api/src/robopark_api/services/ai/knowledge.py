"""Scoped, bounded lexical retrieval with explicit provenance and tombstones."""

import hashlib
import re
import time
import unicodedata
from collections import Counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import case, delete, func, or_, select

from robopark_api.ai_models import AIChunk, AIDocument, AITerm
from robopark_api.services.ai import policy, retrieval

STOP = {
    "это",
    "как",
    "что",
    "для",
    "или",
    "при",
    "его",
    "она",
    "они",
    "the",
    "and",
    "на",
    "по",
    "не",
    "от",
    "из",
    "со",
    "до",
}
SYNONYMS = {
    "провод": "кабел",
    "проводк": "кабел",
    "акб": "аккумулятор",
    "батаре": "аккумулятор",
    "парктроник": "ultrasonic",
    "лидар": "lidar",
    "моторконтроллер": "motorcontrol",
}


def redact(text):
    text = str(text).replace("\x00", "")
    text = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[скрыто]@", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [скрыто]", text)
    text = re.sub(
        r"(?im)(authorization\s*[:=]\s*(?:bearer\s+)?|(?:api[_-]?key|token|password|пароль|секрет)\s*[:=]\s*)[^\s,;]+",
        r"\1[скрыто]",
        text,
    )
    text = re.sub(
        r"https?://[^\s<>]+",
        lambda m: re.sub(
            r"([?&](?:token|key|secret|password|auth)=)[^&#]*", r"\1[скрыто]", m[0], flags=re.I
        ),
        text,
    )
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[контакт скрыт]", text, flags=re.I)
    text = re.sub(
        r"(?<!\w)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\w)",
        "[телефон скрыт]",
        text,
    )
    return text.strip()


def tokens(text):
    words = re.findall(
        r"[a-zа-я0-9]+(?:-[a-z0-9]+)?",
        unicodedata.normalize("NFKC", text).lower().replace("ё", "е"),
    )
    result = []
    for word in words:
        if len(word) < 2 or len(word) > 80 or word in STOP:
            continue
        if re.fullmatch(r"[а-я]{5,}", word):
            word = re.sub(
                r"(?:иями|ами|ями|ого|ему|ыми|ими|его|ому|ов|ев|ах|ях|ая|яя|ое|ее|ые|ие|ый|ий|ой|ую|юю|ом|ем|ы|и|а|я|у|ю|е|ь)$",
                "",
                word,
            )
        result.append(SYNONYMS.get(word, word))
    return result


def scoped(db, user, park_id=None):
    allowed = policy.parks(db, user)
    if park_id is not None:
        policy.park(db, user, park_id)
        allowed = {park_id}
    return or_(AIDocument.park_id.is_(None), AIDocument.park_id.in_(allowed))


def reindex(db, document):
    chunk_ids = select(AIChunk.id).where(AIChunk.document_id == document.id)
    db.execute(delete(AITerm).where(AITerm.chunk_id.in_(chunk_ids)))
    db.execute(delete(AIChunk).where(AIChunk.document_id == document.id))
    if document.state != "active":
        return
    terms = []
    for ordinal, start in enumerate(range(0, len(document.content), 1050)):
        text = document.content[start : start + 1200]
        chunk = AIChunk(id=str(uuid4()), document_id=document.id, ordinal=ordinal, content=text)
        db.add(chunk)
        counts = Counter(tokens(document.title + " " + text))
        terms.extend(
            AITerm(term=term, chunk_id=chunk.id, weight=min(count, 10))
            for term, count in counts.items()
        )
    db.flush()
    db.add_all(terms)


def add(db, user, value, *, trust=None):
    data = value.model_dump() if hasattr(value, "model_dump") else dict(value)
    policy.park(db, user, data.get("park_id"), global_allowed=True)
    return _add_document(db, data, created_by=user.id, trust=trust)


def _add_document(db, data, *, created_by, trust=None, stable_source=False):
    content = redact(data["content"])
    title = redact(data["title"])[:250]
    if not content or not title:
        raise HTTPException(422, "ai_document_empty")
    scope = str(data.get("park_id"))
    fingerprint = hashlib.sha256((scope + "\n" + content).encode()).hexdigest()
    source_ref = redact(data.get("source_ref", ""))[:400]
    source_identity = (
        source_ref
        if stable_source and source_ref
        else data["kind"] + "\n" + (source_ref or fingerprint)
    )
    source_key = hashlib.sha256((scope + "\n" + source_identity).encode()).hexdigest()
    identity = AIDocument.source_key == source_key
    if not stable_source:
        identity = or_(identity, AIDocument.fingerprint == fingerprint)
    existing = db.scalar(select(AIDocument).where(identity).limit(1))
    if existing:
        return existing, False
    row = AIDocument(
        source_key=source_key,
        fingerprint=fingerprint,
        title=title,
        content=content,
        kind=data["kind"],
        state=data.get("state", "candidate"),
        park_id=data.get("park_id"),
        source_ref=source_ref,
        trust=trust or ("instruction" if data["kind"] == "manual" else "unverified"),
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    reindex(db, row)
    return row, True


def view(row, *, content=False):
    result = policy.public_columns(row, ("source_key", "fingerprint", "created_by", "content"))
    if content:
        result["content"] = row.content
    return result


def get(db, user, document_id):
    row = db.scalar(
        select(AIDocument).where(
            AIDocument.id == document_id, scoped(db, user), AIDocument.state != "deleted"
        )
    )
    if row is None or (row.state != "active" and not policy.can_manage(db, user)):
        raise HTTPException(404, "ai_not_found")
    return row


def search(db, user, query, *, park_id=None, limit=6, context=""):
    request = retrieval.prepare(query, tokens, context)
    if not request.keys or not request.terms:
        return []
    scope = scoped(db, user, park_id)
    filters = [scope, AIDocument.state == "active", AITerm.term.in_(request.terms)]
    frequencies = dict(
        db.execute(
            select(AITerm.term, func.count())
            .join(AIChunk, AIChunk.id == AITerm.chunk_id)
            .join(AIDocument, AIDocument.id == AIChunk.document_id)
            .where(*filters)
            .group_by(AITerm.term)
        ).all()
    )
    if not frequencies:
        return []
    idf = retrieval.frequency_weights(frequencies)
    term_weight = case(idf, value=AITerm.term, else_=1.0)
    # Scope before both frequency calculation and bounded candidate selection.
    # A manual reserve prevents common chat words from crowding out procedures.
    score = func.sum(term_weight * case((AITerm.weight > 2, 2), else_=AITerm.weight))
    statement = (
        select(AIChunk, AIDocument, score.label("score"))
        .join(AITerm, AITerm.chunk_id == AIChunk.id)
        .join(AIDocument, AIDocument.id == AIChunk.document_id)
        .where(*filters)
        .group_by(AIChunk.id, AIDocument.id)
        .order_by(score.desc(), AIDocument.source_key, AIChunk.ordinal)
    )
    # Hard requirements apply before the candidate cap. Otherwise unrelated
    # high-frequency chunks can evict the only exact match before reranking.
    for code in request.exact_codes:
        statement = statement.where(
            func.lower(AIDocument.title + "\n" + AIChunk.content).regexp_match(
                retrieval.code_pattern(code)
            )
        )
    for entity in request.entities:
        statement = statement.having(
            func.sum(case((AITerm.term.in_(retrieval.alias_terms(tokens)[entity]), 1), else_=0)) > 0
        )
    rows = list(db.execute(statement.limit(160)).all())
    if request.procedure:
        rows.extend(
            db.execute(
                statement.where(
                    or_(
                        AIDocument.kind == "manual",
                        AIDocument.title.startswith("Неполная инструкция:"),
                    )
                ).limit(40)
            ).all()
        )
    if request.mapping:
        rows.extend(db.execute(statement.where(AIDocument.kind == "note").limit(40)).all())
    ranked, seen_chunks = [], set()
    for chunk, doc, _ in rows:
        if chunk.id in seen_chunks:
            continue
        seen_chunks.add(chunk.id)
        relevance = retrieval.rank(
            request,
            doc.title,
            chunk.content,
            kind=doc.kind,
            trust=doc.trust,
            tokenize=tokens,
            idf=idf,
        )
        if relevance > 0:
            ranked.append((relevance, doc.source_key, chunk.ordinal, chunk, doc))
    ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
    result, seen = [], set()
    for _, _, _, chunk, doc in ranked:
        if doc.id in seen:
            continue
        seen.add(doc.id)
        result.append(
            {
                "id": doc.id,
                "title": doc.title,
                "excerpt": retrieval.excerpt(
                    doc.content,
                    chunk.content,
                    include_start=doc.kind == "manual"
                    or doc.title.startswith("Неполная инструкция:"),
                ),
                "trust": doc.trust,
            }
        )
        if len(result) >= limit:
            break
    return result


def list_documents(db, user, *, query="", park_id=None, state=None, offset=0, limit=30):
    conditions = [scoped(db, user, park_id)]
    if state and state != "deleted" and policy.can_manage(db, user):
        conditions.append(AIDocument.state == state)
    else:
        conditions.append(
            AIDocument.state.in_(("active", "candidate", "rejected"))
            if policy.can_manage(db, user) and not state
            else AIDocument.state == "active"
        )
    if query.strip():
        terms = list(dict.fromkeys(tokens(query)))[:32]
        matching = (
            select(AIChunk.document_id)
            .join(AITerm, AITerm.chunk_id == AIChunk.id)
            .where(AITerm.term.in_(terms))
        )
        escaped = query.strip().replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            or_(AIDocument.id.in_(matching), AIDocument.title.ilike(f"%{escaped}%", escape="\\"))
        )
    total = db.scalar(select(func.count()).select_from(AIDocument).where(*conditions))
    rows = db.scalars(
        select(AIDocument)
        .where(*conditions)
        .order_by(AIDocument.updated_at.desc(), AIDocument.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": [view(row) for row in rows], "total": total, "offset": offset, "limit": limit}


def edit(db, user, row, value):
    changes = value.model_dump(exclude_none=True, exclude={"revision"})
    if "content" in changes:
        changes["content"] = redact(changes["content"])
        changes["fingerprint"] = hashlib.sha256(
            (str(row.park_id) + "\n" + changes["content"]).encode()
        ).hexdigest()
    if "title" in changes:
        changes["title"] = redact(changes["title"])[:250]
    if any(not changes[key] for key in ("content", "title") if key in changes):
        raise HTTPException(422, "ai_document_empty")
    # Keep the update and index transaction atomic, including optimistic CAS.
    from sqlalchemy import update

    count = db.execute(
        update(AIDocument)
        .where(AIDocument.id == row.id, AIDocument.revision == value.revision)
        .values(**changes, revision=value.revision + 1, updated_at=time.time())
        .execution_options(synchronize_session=False)
    ).rowcount
    if count != 1:
        db.rollback()
        raise HTTPException(409, "ai_revision_conflict")
    db.refresh(row)
    reindex(db, row)
    db.commit()
    return row


def remove(db, row):
    row.state = "deleted"
    row.content = ""
    row.title = "Удалённый источник"
    row.revision += 1
    row.updated_at = time.time()
    reindex(db, row)
    db.commit()
