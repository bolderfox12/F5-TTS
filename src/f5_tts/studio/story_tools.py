"""Character scripts reuse the narration renderer and existing saved voices/models."""

import re
from collections import defaultdict, deque
from uuid import uuid4


def split_story_text(text, limit=1200):
    words, chunks, current = text.split(), [], ""
    for word in words:
        if current and len(current) + len(word) + 1 > limit:
            chunks.append(current)
            current = ""
        current += (" " if current else "") + word
    if current:
        chunks.append(current)
    return chunks


def parse_character_script(script, characters):
    """Named speaker lines start turns; continuation lines keep the current speaker."""
    by_name = {character["name"].casefold(): character for character in characters}
    current = characters[0]
    turns, lines = [], []

    def flush():
        for text in split_story_text(" ".join(lines)):
            turns.append(
                {"character": current["id"], "text": text, "voice": current["voice"], "model_id": current["model_id"]}
            )
        lines.clear()

    for index, line in enumerate((script or "").splitlines(), 1):
        line = line.strip()
        if not line:
            flush()
            continue
        match = re.match(r"^([^:：]{1,60})[:：]\s*(.*)$", line)
        if match:
            name, text = match.groups()
            speaker = by_name.get(name.strip().casefold())
            if speaker is None:
                raise ValueError(f"Personagem não cadastrado na linha {index}: {name.strip()}")
            flush()
            current = speaker
            lines.append(text)
        else:
            lines.append(line)
    flush()
    if not turns:
        raise ValueError("Escreva as falas da história antes de salvar.")
    return turns


class StoryTools:
    def stories(self):
        return [project for project in self.narrations() if project.get("kind") == "story"]

    def validate_characters(self, characters):
        if not characters or len(characters) > 50:
            raise ValueError("Adicione de 1 a 50 personagens ao elenco.")
        voices = {voice["id"] for voice in self.read()["voices"]}
        models = {model["id"] for model in self.models()}
        names, identifiers, result = set(), set(), []
        for character in characters:
            name = str(character.get("name", "")).strip()
            identifier = character.get("id") or uuid4().hex
            if not name or len(name) > 60 or any(char in name for char in ":：\n\r"):
                raise ValueError("Use nomes de personagens com até 60 caracteres, sem dois-pontos.")
            if name.casefold() in names or identifier in identifiers:
                raise ValueError("Cada personagem precisa de um nome e identificador únicos.")
            if character.get("voice") not in voices:
                raise ValueError(f"Escolha uma voz salva para o personagem: {name}")
            model_id = character.get("model_id") or "original"
            if model_id not in models:
                raise ValueError(f"Escolha um modelo cadastrado para o personagem: {name}")
            names.add(name.casefold())
            identifiers.add(identifier)
            result.append({"id": identifier, "name": name, "voice": character["voice"], "model_id": model_id})
        return result

    def save_story(self, project_id, name, script, characters):
        if not name or not name.strip():
            raise ValueError("Dê um nome à história.")
        with self._storage:
            characters = self.validate_characters(characters)
            turns = parse_character_script(script, characters)
            data = self.read()
            project = next((item for item in data.get("narrations", []) if item["id"] == project_id), None)
            if project_id and (project is None or project.get("kind") != "story"):
                raise ValueError("Abra uma história existente.")
            reusable = defaultdict(deque)
            for block in project["blocks"] if project else []:
                reusable[
                    (block.get("character"), block["text"], block["voice"], block.get("model_id", "original"))
                ].append(block)
            blocks = []
            for turn in turns:
                candidates = reusable[(turn["character"], turn["text"], turn["voice"], turn["model_id"])]
                blocks.append(
                    candidates.popleft()
                    if candidates
                    else {
                        "id": uuid4().hex,
                        **turn,
                        "pause": 0.4,
                        "render": None,
                    }
                )
            if project is None:
                project = {"id": uuid4().hex, "kind": "story"}
                data.setdefault("narrations", []).append(project)
            project.update(name=name.strip(), characters=characters, blocks=blocks, mix=None)
            self.write(data)
        return self.narration(project["id"])

    @staticmethod
    def story_script(project):
        names = {character["id"]: character["name"] for character in project.get("characters", [])}
        return "\n\n".join(f"{names.get(block.get('character'), '')}: {block['text']}" for block in project["blocks"])

    def edit_story_block(self, project_id, block_id, text, character_id, pause):
        if not text or not text.strip() or not 0 <= float(pause) <= 10:
            raise ValueError("Escreva a fala e escolha uma pausa entre 0 e 10 segundos.")
        with self._storage:
            data = self.read()
            project = next((item for item in data.get("narrations", []) if item["id"] == project_id), None)
            character = (
                next((c for c in project.get("characters", []) if c["id"] == character_id), None) if project else None
            )
            block = next((b for b in project["blocks"] if b["id"] == block_id), None) if project else None
            if character is None or block is None:
                raise ValueError("Selecione uma fala e um personagem da história.")
            block.update(
                text=text.strip(),
                character=character_id,
                voice=character["voice"],
                model_id=character["model_id"],
                pause=float(pause),
            )
            project["mix"] = None
            self.write(data)
        return self.narration(project_id)

    def delete_story_block(self, project_id, block_id):
        with self._storage:
            data = self.read()
            project = next((item for item in data.get("narrations", []) if item["id"] == project_id), None)
            if (
                project is None
                or project.get("kind") != "story"
                or not any(b["id"] == block_id for b in project["blocks"])
            ):
                raise ValueError("Selecione uma fala da história.")
            project["blocks"] = [block for block in project["blocks"] if block["id"] != block_id]
            project["mix"] = None
            self.write(data)
        return self.narration(project_id)
