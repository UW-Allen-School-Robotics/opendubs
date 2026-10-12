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


def cue_nodes(text):
    """The `cues:` sequence node and its items."""
    root = _root(text)
    _, seq = _key(root, "cues")
    if seq is None:
        raise EditError("the show has no cues: list")
    if not isinstance(seq, yaml.SequenceNode):
        raise EditError("cues: must be a list")
    return seq


def cue_lines(text) -> list[int]:
    """1-based line number of each cue, in file order — for 'line N' in the editor."""
    return [n.start_mark.line + 1 for n in cue_nodes(text).value]


def _line_bounds(text, start, end):
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return ls, (len(text) if le < 0 else le + 1)


def _set_cue(text, seq, index, new, key="cues"):
    if not 0 <= index < len(seq.value):
        raise EditError(f"no cue {index}")
    node = seq.value[index]
    if not isinstance(node, yaml.MappingNode):
        raise EditError(f"cue {index} is not a mapping")
    if not isinstance(new, dict) or not new:
        raise EditError("a cue must be a non-empty mapping")
    old = yaml.safe_load(text)[key][index]
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
        raise EditError("add the first cue by hand: an empty cues: list has no indentation to copy")
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


def _set_music(text, key, value):
    """Set one field of the music block, or remove it with value None (song and file only: the
    tempo grid stays when a show's music is taken out, so bars and beats keep their meaning)."""
    if key not in ("bpm", "offset_s", "beats_per_bar", "file", "song"):
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
        elif op == "note_add":
            text = _note_add(text, ed.get("note"))
        elif op == "note_set":
            text = _set_cue(text, _notes_seq(text), _note_index(text, ed.get("id")), ed.get("note"), NOTES)
        elif op == "note_delete":
            text = _delete_cue(text, _notes_seq(text), _note_index(text, ed.get("id")))
        elif op in ("set", "delete", "add"):
            seq = cue_nodes(text)
            if op == "set":
                text = _set_cue(text, seq, int(ed["index"]), ed.get("cue"))
            elif op == "delete":
                text = _delete_cue(text, seq, int(ed["index"]))
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
