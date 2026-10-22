"""Edit a show's YAML TEXT, surgically, so a round trip through the composer is a minimal diff.

WHY NOT load-and-dump: PyYAML drops comments, and a show carries its reasoning in comments
(`hello.yaml` explains every safety-relevant choice in one). A dump would also restyle every line,
so an editor save would be an unreadable diff — exactly what standing rule 7 forbids, and what makes
a file un-reviewable by an agent or a human (choreo-format-v1.md 9).

So edits address SOURCE SPANS found with yaml.compose():
  * change a field's value      -> replace only that value's characters
  * add or remove a field       -> rewrite that one cue as a one-line flow mapping, original key
                                   order kept, new keys appended
  * delete a cue                -> remove its line(s)
  * add a cue                   -> append one line at the end of `cues:`
  * change a music: scalar      -> replace that value in place

An unedited file round-trips byte-identical. Standard library + PyYAML only.
"""
from __future__ import annotations

import json
import math
import re

import yaml


class EditError(Exception):
    pass


_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
# Plain scalars YAML 1.1 would NOT read back as the same string.
_YAML_SPECIAL = {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "~"}


def fmt(v) -> str:
    """One value in flow style, the shortest form that reads back as the same value."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise EditError(f"cannot write {v!r} into a show")
        r = round(v, 6)
        if r == int(r) and abs(r) < 1e15:
            return f"{int(r)}.0" if r != 0 else "0.0"
        return repr(r)
    if isinstance(v, str):
        if _PLAIN.match(v) and v.lower() not in _YAML_SPECIAL:
            return v
        # a JSON string is a valid YAML double-quoted scalar; prose (notes) keeps its own characters
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{fmt_key(k)}: {fmt(x)}" for k, x in v.items()) + " }"
    raise EditError(f"cannot write a {type(v).__name__} into a show")


def fmt_key(k) -> str:
    if isinstance(k, str) and re.fullmatch(r"\d+", k):
        return k                      # joint numbers: written bare, read back as ints
    return fmt(k)


def _norm(x):
    """Compare a YAML-loaded value with a JSON-delivered one: keys become strings."""
    return json.loads(json.dumps(x, sort_keys=True, default=str))


def _root(text):
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):
        raise EditError("a show must be a mapping at the top level")
    return node


def _key(mapping, name):
    for k, v in mapping.value:
        if isinstance(k, yaml.ScalarNode) and k.value == name:
            return k, v
    return None, None


def _end(node):
    """Where a node's own text ends. A BLOCK mapping's end_mark sits at the next token, which
    swallows the comments and blank lines after it; its last value's end does not."""
    if isinstance(node, yaml.MappingNode) and not node.flow_style and node.value:
        return _end(node.value[-1][1])
    if isinstance(node, yaml.SequenceNode) and not node.flow_style and node.value:
        return _end(node.value[-1])
    return node.end_mark.index


def cue_nodes(text, clip=None):
    """The `cues:` sequence node — the show's, or with `clip`, that clip's own (clips.<name>.cues)."""
    root = _root(text)
    where = "cues:"
    if clip is not None:
        _, clips = _key(root, "clips")
        _, body = _key(clips, clip) if isinstance(clips, yaml.MappingNode) else (None, None)
        if not isinstance(body, yaml.MappingNode):
            raise EditError(f"no clip {clip!r}")
        root, where = body, f"clip {clip}'s cues:"
    _, seq = _key(root, "cues")
    if seq is None:
        raise EditError(f"the show has no {where} list")
    if not isinstance(seq, yaml.SequenceNode):
        raise EditError(f"{where} must be a list")
    return seq


def _cues_path(clip=None):
    return ["clips", clip, "cues"] if clip is not None else ["cues"]


def cue_lines(text, clip=None) -> list[int]:
    """1-based line number of each cue, in file order — for 'line N' in the editor."""
    return [n.start_mark.line + 1 for n in cue_nodes(text, clip).value]


def _line_bounds(text, start, end):
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return ls, (len(text) if le < 0 else le + 1)


def _set_cue(text, seq, index, new, key="cues"):
    """`key` is the list's name at the top level, or a path to it (["clips", name, "cues"])."""
    if not 0 <= index < len(seq.value):
        raise EditError(f"no cue {index}")
    node = seq.value[index]
    if not isinstance(node, yaml.MappingNode):
        raise EditError(f"cue {index} is not a mapping")
    if not isinstance(new, dict) or not new:
        raise EditError("a cue must be a non-empty mapping")
    old = yaml.safe_load(text)
    for k in ([key] if isinstance(key, str) else key):
        old = old[k]
    old = old[index]
    old_keys = [k.value for k, _ in node.value]
    if set(map(str, old_keys)) == set(map(str, new)):
        # same fields: replace only the values that changed, back to front
        reps = []
        for k, v in node.value:
            if _norm(old.get(_typed_key(old, k.value))) != _norm(new[str(k.value)]):
                reps.append((v.start_mark.index, _end(v), fmt(new[str(k.value)])))
        for s, e, txt in sorted(reps, reverse=True):
            text = text[:s] + txt + text[e:]
        return text
    keys = [k for k in old_keys if str(k) in new] + [k for k in new if k not in map(str, old_keys)]
    body = "{ " + ", ".join(f"{fmt_key(str(k))}: {fmt(new[str(k)])}" for k in keys) + " }"
    return text[:node.start_mark.index] + body + text[_end(node):]


def _typed_key(d, raw):
    for k in d:
        if str(k) == str(raw):
            return k
    return raw


def _delete_cue(text, seq, index):
    if not 0 <= index < len(seq.value):
        raise EditError(f"no cue {index}")
    node = seq.value[index]
    s, e = node.start_mark.index, _end(node)
    ls, le = _line_bounds(text, s, e)
    if text[ls:s].strip() != "-":
        raise EditError("can only delete a cue that starts its own line (`- ...`)")
    return text[:ls] + text[le:]


def _add_cue(text, seq, cue):
    if not isinstance(cue, dict) or not cue:
        raise EditError("a cue must be a non-empty mapping")
    line = "- " + fmt({str(k): v for k, v in cue.items()})
    if not seq.value:
        # the first cue: an empty flow list (`cues: []`) becomes a block list with it
        if not seq.flow_style:
            raise EditError("add the first cue by hand: an empty cues: list has no indentation to copy")
        ls = text.rfind("\n", 0, seq.start_mark.index) + 1
        key_line = text[ls:seq.start_mark.index].rstrip()
        le = text.find("\n", seq.start_mark.index)
        le = len(text) if le < 0 else le + 1
        indent = " " * (len(key_line) - len(key_line.lstrip()) + 2)    # under its own key, however deep
        return text[:ls] + key_line + "\n" + indent + line + "\n" + text[le:]
    last = seq.value[-1]
    s, e = last.start_mark.index, _end(last)
    ls, le = _line_bounds(text, s, e)
    dash = text[ls:s]
    if dash.strip() != "-":
        raise EditError("cues: must be a block list (one `- ...` per line) to append to")
    indent = dash[:dash.index("-")]
    insert = indent + line + "\n"
    if le == len(text) and not text.endswith("\n"):
        insert = "\n" + insert
    return text[:le] + insert + text[le:]


def _insert_cue(text, seq, index, cue):
    if not isinstance(cue, dict) or not cue:
        raise EditError("a cue must be a non-empty mapping")
    if not seq.value or index >= len(seq.value):
        return _add_cue(text, seq, cue)
    if index < 0:
        index = max(0, len(seq.value) + index)
    node = seq.value[index]
    s, e = node.start_mark.index, _end(node)
    ls, _ = _line_bounds(text, s, e)
    prefix = text[ls:s]
    line = f"{prefix}{fmt({str(k): v for k, v in cue.items()})}\n"
    return text[:ls] + line + text[ls:]


def _set_music(text, key, value):
    """Set one field of the music block, or remove it with value None (song and file only: the
    tempo grid stays when a show's music is taken out, so bars and beats keep their meaning)."""
    if key not in ("bpm", "offset_s", "beats_per_bar", "file", "song", "role"):
        raise EditError(f"music: has no field {key!r}")
    if value is None:
        if key not in ("file", "song"):
            raise EditError(f"music: {key} cannot be removed — set it instead")
        return _drop_music_key(text, key)
    root = _root(text)
    _, m = _key(root, "music")
    if m is None:
        kc, _ = _key(root, "cues")
        ls = text.rfind("\n", 0, kc.start_mark.index) + 1
        block = {"bpm": 120, "offset_s": 0.0, "beats_per_bar": 4}
        block[key] = value
        return text[:ls] + "music: " + fmt(block) + "\n" + text[ls:]
    if not isinstance(m, yaml.MappingNode):
        raise EditError("music: is not a mapping")
    _, v = _key(m, key)
    if v is not None:
        return text[:v.start_mark.index] + fmt(value) + text[_end(v):]
    if m.flow_style:
        cur = yaml.safe_load(text)["music"]
        cur[key] = value
        return text[:m.start_mark.index] + fmt(cur) + text[_end(m):]
    lastk = m.value[-1][0]
    ls, le = _line_bounds(text, lastk.start_mark.index, _end(m.value[-1][1]))
    indent = " " * lastk.start_mark.column
    return text[:le] + f"{indent}{key}: {fmt(value)}\n" + text[le:]


def _drop_music_key(text, key):
    root = _root(text)
    _, m = _key(root, "music")
    if m is None or not isinstance(m, yaml.MappingNode):
        return text                                   # nothing to remove
    for i, (k, v) in enumerate(m.value):
        if k.value != key:
            continue
        if m.flow_style or len(m.value) == 1:
            cur = yaml.safe_load(text)["music"]
            cur.pop(key, None)
            return text[:m.start_mark.index] + fmt(cur) + text[_end(m):]
        ls, le = _line_bounds(text, k.start_mark.index, _end(v))
        if text[ls:k.start_mark.index].strip():
            raise EditError(f"music: {key} does not start its own line — edit the YAML by hand")
        return text[:ls] + text[le:]
    return text


def _declare(text, slot, on: bool):
    """Add a slot to the show's `slots:` list (or take one out). A flow list is rewritten in the
    validator's slot order; a block list gains or loses one line; no `slots:` key gets one, above
    `cues:`. Declaring what is declared, or undeclaring what is not, changes nothing."""
    if not isinstance(slot, str) or not slot:
        raise EditError("declare needs a slot name")
    root = _root(text)
    _, v = _key(root, "slots")
    if v is None:
        if not on:
            return text
        kc, _ = _key(root, "cues")
        if kc is None:
            raise EditError("the show has no cues: list")
        ls = text.rfind("\n", 0, kc.start_mark.index) + 1
        return text[:ls] + "slots: " + fmt([slot]) + "\n" + text[ls:]
    if not isinstance(v, yaml.SequenceNode):
        raise EditError("slots: must be a list")
    cur = [n.value for n in v.value]
    if (slot in cur) == on:
        return text
    if v.flow_style or not v.value:
        new = cur + [slot] if on else [x for x in cur if x != slot]
        import cue                                    # the validator's own slot order, not a copy of it
        order = {s: i for i, s in enumerate(cue.SLOTS)}
        new.sort(key=lambda s: order.get(s, len(order)))
        return text[:v.start_mark.index] + fmt(new) + text[_end(v):]
    if on:
        last = v.value[-1]
        ls, le = _line_bounds(text, last.start_mark.index, _end(last))
        indent = text[ls:last.start_mark.index]
        return text[:le] + f"{indent}{fmt(slot)}\n" + text[le:]
    node = next(n for n in v.value if n.value == slot)
    ls, le = _line_bounds(text, node.start_mark.index, _end(node))
    return text[:ls] + text[le:]


def _set_top(text, key, value):
    """Set a top-level field: a plain id or name (a missing one becomes the first line), or the
    `clearance:` stamp sweep.py writes (replaced whole; a missing one goes just above `cues:`)."""
    if key not in ("id", "name", "clearance"):
        raise EditError(f"top-level field {key!r} is not editable this way")
    root = _root(text)
    k, v = _key(root, key)
    if v is None:
        if key == "clearance":
            kc, _ = _key(root, "cues")
            if kc is None:
                raise EditError("the show has no cues: list")
            ls = text.rfind("\n", 0, kc.start_mark.index) + 1
            return text[:ls] + f"{key}: {fmt(value)}\n" + text[ls:]
        return f"{key}: {fmt(value)}\n" + text
    if key == "clearance":
        ls, le = _line_bounds(text, k.start_mark.index, _end(v))
        return text[:ls] + f"{key}: {fmt(value)}\n" + text[le:]
    if not isinstance(v, yaml.ScalarNode):
        raise EditError(f"{key}: is not a plain value")
    return text[:v.start_mark.index] + fmt(value) + text[_end(v):]


def _require(text, key, value):
    """Set one entry of the show's `requires:` mapping (added above `cues:` if there is none)."""
    if not isinstance(key, str) or not key:
        raise EditError("require needs a key")
    root = _root(text)
    _, m = _key(root, "requires")
    if m is None:
        kc, _ = _key(root, "cues")
        if kc is None:
            raise EditError("the show has no cues: list")
        ls = text.rfind("\n", 0, kc.start_mark.index) + 1
        return text[:ls] + "requires: " + fmt({key: value}) + "\n" + text[ls:]
    if not isinstance(m, yaml.MappingNode):
        raise EditError("requires: is not a mapping")
    _, v = _key(m, key)
    if v is not None:
        return text[:v.start_mark.index] + fmt(value) + text[_end(v):]
    if m.flow_style or not m.value:
        cur = yaml.safe_load(text).get("requires") or {}
        cur[key] = value
        return text[:m.start_mark.index] + fmt(cur) + text[_end(m):]
    lastk = m.value[-1][0]
    ls, le = _line_bounds(text, lastk.start_mark.index, _end(m.value[-1][1]))
    return text[:le] + f"{' ' * lastk.start_mark.column}{key}: {fmt(value)}\n" + text[le:]


CLIP_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")


def _clip_block(name, clip, indent):
    """A clip definition as block text: its length (and notes) as plain lines, its cues one per line."""
    pad, lines = " " * indent, [f"{' ' * indent}{name}:"]
    for k, v in clip.items():
        if k != "cues":
            lines.append(f"{pad}  {k}: {fmt(v)}")
    cues = clip.get("cues") or []
    if cues:
        lines.append(f"{pad}  cues:")
        lines += [f"{pad}    - {fmt({str(a): b for a, b in c.items()})}" for c in cues]
    else:
        lines.append(f"{pad}  cues: []")
    return "\n".join(lines) + "\n"


def _clip_set(text, name, clip):
    """Create a clip, or replace one whole: `clips:` is made above `cues:` if the show has none."""
    if not isinstance(name, str) or not CLIP_NAME.fullmatch(name):
        raise EditError("a clip's name is letters, digits, - and _ (starting with a letter)")
    if not isinstance(clip, dict):
        raise EditError("a clip is a mapping {length_beats or length_s, cues}")
    root = _root(text)
    k, clips = _key(root, "clips")
    if clips is None:
        kc, _ = _key(root, "cues")
        if kc is None:
            raise EditError("the show has no cues: list")
        ls = text.rfind("\n", 0, kc.start_mark.index) + 1
        return text[:ls] + "clips:\n" + _clip_block(name, clip, 2) + "\n" + text[ls:]
    if not isinstance(clips, yaml.MappingNode) or clips.flow_style:
        raise EditError("clips: must be a block mapping to edit it here")
    indent = clips.value[0][0].start_mark.column if clips.value else 2
    for ck, cv in clips.value:
        if ck.value == name:                      # replace this clip's whole block
            ls, le = _line_bounds(text, ck.start_mark.index, _end(cv))
            return text[:ls] + _clip_block(name, clip, indent) + text[le:]
    last_k, last_v = clips.value[-1]
    _, le = _line_bounds(text, last_k.start_mark.index, _end(last_v))
    return text[:le] + _clip_block(name, clip, indent) + text[le:]


NOTES = "timeline_notes"


def _notes_seq(text):
    _, seq = _key(_root(text), NOTES)
    if seq is not None and not isinstance(seq, yaml.SequenceNode):
        raise EditError(f"{NOTES}: must be a list")
    return seq


def _note_index(text, note_id):
    for i, n in enumerate(yaml.safe_load(text).get(NOTES) or []):
        if isinstance(n, dict) and n.get("id") == note_id:
            return i
    raise EditError(f"no timeline note {note_id!r}")


def _note_add(text, note):
    """Append a note. A show with no notes yet gets a `timeline_notes:` block above `cues:`."""
    if not isinstance(note, dict) or not note.get("id"):
        raise EditError("a timeline note needs an id")
    seq = _notes_seq(text)
    line = "- " + fmt({str(k): v for k, v in note.items()})
    if seq is None or not seq.value:
        root = _root(text)
        if seq is not None:                            # `timeline_notes: []` — replace it with a block
            k, _ = _key(root, NOTES)
            ls, le = _line_bounds(text, k.start_mark.index, _end(seq))
            return text[:ls] + f"{NOTES}:\n  {line}\n" + text[le:]
        kc, _ = _key(root, "cues")
        if kc is None:
            raise EditError("the show has no cues: list")
        ls = text.rfind("\n", 0, kc.start_mark.index) + 1
        return text[:ls] + f"{NOTES}:\n  {line}\n" + text[ls:]
    return _add_cue(text, seq, note)


def apply(text: str, edits: list) -> str:
    """Apply edits in order. Each edit is one of:
        {"op": "set", "index": i, "cue": {...whole cue...}}
        {"op": "delete", "index": i}
        {"op": "add", "cue": {...}}
        {"op": "music", "key": "bpm", "value": 121.5}
        {"op": "music", "key": "song", "value": None}      # None removes song or file
        {"op": "declare", "slot": "GRIP_R"}                 # add to slots: (undeclare removes)
        {"op": "note_add", "note": {...}}                   # timeline notes, addressed by id
        {"op": "note_set", "id": "n1", "note": {...whole note...}}
        {"op": "note_delete", "id": "n1"}
    Indices refer to the file as it is when that edit is applied (so delete back to front)."""
    for ed in edits:
        op = ed.get("op")
        if op == "music":
            text = _set_music(text, ed.get("key"), ed.get("value"))
        elif op in ("declare", "undeclare"):
            text = _declare(text, ed.get("slot"), op == "declare")
        elif op == "require":
            text = _require(text, ed.get("key"), ed.get("value"))
        elif op == "top":
            text = _set_top(text, ed.get("key"), ed.get("value"))
        elif op == "note_add":
            text = _note_add(text, ed.get("note"))
        elif op == "note_set":
            text = _set_cue(text, _notes_seq(text), _note_index(text, ed.get("id")), ed.get("note"), NOTES)
        elif op == "note_delete":
            text = _delete_cue(text, _notes_seq(text), _note_index(text, ed.get("id")))
        elif op == "clip_set":
            text = _clip_set(text, ed.get("name"), ed.get("clip"))
        elif op in ("set", "delete", "add", "insert"):
            clip = ed.get("clip_name")                 # a clip's own cues, when editing inside it
            seq = cue_nodes(text, clip)
            if op == "set":
                text = _set_cue(text, seq, int(ed["index"]), ed.get("cue"), _cues_path(clip))
            elif op == "delete":
                text = _delete_cue(text, seq, int(ed["index"]))
            elif op == "insert":
                text = _insert_cue(text, seq, int(ed["index"]), ed.get("cue"))
            else:
                text = _add_cue(text, seq, ed.get("cue"))
        else:
            raise EditError(f"unknown edit op {op!r}")
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:          # pragma: no cover — a bug here, not user error
            raise EditError(f"edit produced invalid YAML: {e}") from e
        if not isinstance(doc, dict):
            raise EditError("edit produced a non-mapping document")
    return text
