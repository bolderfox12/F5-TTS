"""Stepwise, resumable editorial analysis of creator-supplied community research."""

import json
from uuid import uuid4

from f5_tts.studio.script_review import local_review_models, request_text

PROMPTS = {
    "clean": "Extract only posts present in the supplied material. Return JSON {posts:[{title,comments,upvotes}]}. Do not infer or invent missing numbers. Ignore usernames, dates and interface text. Keep at most 100 posts.",
    "audience": "Analyze only the supplied post data. Return JSON {pains:[...], desires:[...], practical_needs:[...], patterns:[...]}. Clearly say when counts or evidence are insufficient. Do not invent demographics or represent this sample as all Reddit users.",
    "ideas": "Create 1 to 10 original YouTube ideas based only on the provided audience analysis. Return JSON {ideas:[{title,angle,reason,source_post}]}. Rank by clarity of viewer value and fit, never claim a predicted CTR or virality. Preserve uncertainty and do not copy post titles verbatim.",
}


def research_step(service, channel_id, research_id, step, text, provider, model):
    if step not in PROMPTS:
        raise ValueError("Escolha uma etapa de pesquisa disponível.")
    if provider == "gemini" and (service.read().get("offline") or not service.review_key_active(provider)):
        raise ValueError("Ative o Gemini nas configurações e desative o modo offline para usar este provedor.")
    if provider == "ollama" and model not in local_review_models():
        raise ValueError("Selecione um modelo instalado no Ollama local.")
    data = service.read()
    studio = data.setdefault("youtube_studio", {"channels": [], "opportunities": [], "research": []})
    channel = next((row for row in studio["channels"] if row["id"] == channel_id), None)
    if not channel:
        raise ValueError("Salve o perfil do canal antes de pesquisar.")
    session = next((row for row in studio.setdefault("research", []) if row["id"] == research_id), None)
    if step == "clean":
        source = (text or "").strip()
        if len(source) < 20 or len(source) > 30000:
            raise ValueError("Cole entre 20 e 30.000 caracteres de posts e dados da comunidade.")
        session = session or {"id": research_id or uuid4().hex, "channel_id": channel_id, "steps": {}}
        session["source"] = source
        session["steps"] = {}
        studio["research"] = [row for row in studio["research"] if row["id"] != research_id] + [session]
        payload = {"niche": channel.get("niche", ""), "subniche": channel.get("subniche", ""), "source_data": source}
    else:
        if not session:
            raise ValueError("Comece organizando os posts colados.")
        required = {"audience": "clean", "ideas": "audience"}[step]
        if required not in session["steps"]:
            raise ValueError("Conclua a etapa anterior antes de continuar.")
        payload = (
            {"audience_analysis": session["steps"][required], "source_posts": session["steps"]["clean"]}
            if step == "ideas"
            else session["steps"][required]
        )
    raw = request_text(
        json.dumps(payload, ensure_ascii=False),
        service.review_api_key(provider) if provider == "gemini" else "",
        provider,
        model,
        prompt=PROMPTS[step],
    )
    try:
        result = json.loads(raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        rows = result["posts"] if step == "clean" else result["ideas"] if step == "ideas" else None
        maximum = 100 if step == "clean" else 10
        if rows is not None and (not isinstance(rows, list) or not 1 <= len(rows) <= maximum):
            raise ValueError
        if step == "clean" and any(not isinstance(row.get("title"), str) or not row["title"].strip() for row in rows):
            raise ValueError
        if step == "ideas" and any(
            not all(isinstance(row.get(key), str) for key in ("title", "angle", "reason", "source_post"))
            for row in rows
        ):
            raise ValueError
        if step == "audience" and not all(
            isinstance(result.get(key), list) for key in ("pains", "desires", "practical_needs", "patterns")
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            "A análise não voltou no formato esperado. Os resultados anteriores foram preservados."
        ) from error
    session["steps"][step] = result
    service.write(data)
    return result
