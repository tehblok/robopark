"""Scoped, bounded lexical retrieval with explicit provenance and tombstones."""

import hashlib
import re
import time
import unicodedata
from collections import Counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select

from robopark_api.ai_models import AIChunk, AIDocument, AITerm
from robopark_api.services.ai import policy

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
    content = redact(data["content"])
    title = redact(data["title"])[:250]
    if not content or not title:
        raise HTTPException(422, "ai_document_empty")
    scope = str(data.get("park_id"))
    fingerprint = hashlib.sha256((scope + "\n" + content).encode()).hexdigest()
    source_ref = redact(data.get("source_ref", ""))[:400]
    source_key = hashlib.sha256(
        (scope + "\n" + data["kind"] + "\n" + (source_ref or fingerprint)).encode()
    ).hexdigest()
    existing = db.scalar(
        select(AIDocument)
        .where(or_(AIDocument.source_key == source_key, AIDocument.fingerprint == fingerprint))
        .limit(1)
    )
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
        created_by=user.id,
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


def search(db, user, query, *, park_id=None, limit=6):
    terms = list(dict.fromkeys(tokens(query)))[:32]
    if not terms:
        return []
    score = func.sum(AITerm.weight)
    rows = db.execute(
        select(AIChunk, AIDocument, score.label("score"))
        .join(AITerm, AITerm.chunk_id == AIChunk.id)
        .join(AIDocument, AIDocument.id == AIChunk.document_id)
        .where(AITerm.term.in_(terms), scoped(db, user, park_id), AIDocument.state == "active")
        .group_by(AIChunk.id, AIDocument.id)
        .order_by(score.desc(), AIDocument.id, AIChunk.ordinal)
        .limit(40)
    ).all()
    # At most two chunks from one source; diversity keeps a long manual from
    # consuming the whole prompt. SQL filtering happens before rank/limit.
    result, seen = [], Counter()
    for chunk, doc, _ in rows:
        if seen[doc.id] >= 2:
            continue
        seen[doc.id] += 1
        result.append(
            {"id": doc.id, "title": doc.title, "excerpt": chunk.content, "trust": doc.trust}
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
