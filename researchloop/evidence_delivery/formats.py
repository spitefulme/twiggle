"""Deterministic passive exports. CSV's variable data is canonical JSON in a cell."""

import base64
import csv
import hashlib
import io
import json
from xml.sax.saxutils import escape


def encode(value, *, ascii_only=False):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii_only
    )


def records(proof, status):
    tree = proof.public.method == "tree-test-v2"
    for row in proof.selected(status):
        if tree:
            yield {
                "method": "tree-test",
                "attemptId": row["attemptId"],
                "taskId": row["taskId"],
                "route": row.get("route", []),
                "terminalCategory": row.get("terminalCategory", "incomplete"),
                "directness": row.get("directness", "unknown"),
                "statuses": row["statuses"],
            }
        else:
            groups = {group.get("groupId"): group for group in row["groups"]}
            for card in proof.result.payload["cards"]:
                card_id = card["cardId"]
                current = row["membership"].get(card_id)
                path = []
                while current in groups and current not in path:
                    path.append(current)
                    current = groups[current].get("parentGroupId")
                yield {
                    "method": "card-sort",
                    "responseId": row["responseId"] or row["evidenceId"],
                    "cardId": card_id,
                    "cardLabel": card["label"],
                    "groupPath": list(reversed(path)),
                    "originalLabels": {
                        key: groups[key]["label"]
                        for key in path
                        if "label" in groups[key]
                    },
                    "structuralStatus": row["structuralStatus"],
                    "statuses": row["statuses"],
                }


def _partition(value, block_size=65536, *, ascii_only=False):
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=ascii_only
    )
    buffer = bytearray()
    for part in encoder.iterencode(value):
        data = part.encode("utf-8")
        offset = 0
        while offset < len(data):
            take = min(block_size - len(buffer), len(data) - offset)
            buffer.extend(data[offset : offset + take])
            offset += take
            if len(buffer) == block_size:
                yield bytes(buffer)
                buffer.clear()
    if buffer:
        yield bytes(buffer)


def dictionaries(proof, status):
    if proof.public.method == "card-sort":
        for row in proof.selected(status):
            yield {
                "responseId": row["responseId"] or row["evidenceId"],
                "groups": row["groups"],
            }


def chunks(job, proof):
    status = job.selection.filter_status or None
    summary = {
        key: val for key, val in proof.result.payload.items() if key != "attempts"
    }
    digest = hashlib.sha256()
    for block in _partition(summary):
        digest.update(block)
    for record in records(proof, status):
        digest.update(encode(record).encode("utf-8") + b"\n")
    for dictionary in dictionaries(proof, status):
        digest.update(encode(dictionary).encode("utf-8") + b"\n")
    manifest = {
        "exportId": str(job.pk),
        "method": "tree-test" if proof.public.method == "tree-test-v2" else "card-sort",
        "versionId": str(job.selection.version_id),
        "cohortId": str(job.selection.cohort_id),
        "analysisId": str(job.selection.analysis_id),
        "format": job.format,
        "generatedAt": job.created_at.isoformat(),
        "integrityDigest": digest.hexdigest(),
        "status": status,
    }
    if job.format == "json":
        yield b'{"manifest":'
        yield from _partition(manifest)
        yield b',"result":'
        yield from _partition(summary)
        yield b',"records":['
        for index, record in enumerate(records(proof, status)):
            if index:
                yield b","
            yield from _partition(record)
        yield b'],"groupDictionaries":['
        for index, dictionary in enumerate(dictionaries(proof, status)):
            if index:
                yield b","
            yield from _partition(dictionary)
        yield b"]}\n"
    elif job.format == "csv":
        # All user-controlled values live inside object JSON which starts with
        # '{', not an executable spreadsheet expression. This is reversible.
        line = io.StringIO(newline="")
        writer = csv.writer(line)

        def row(kind, value):
            line.seek(0)
            line.truncate()
            writer.writerow((kind, encode(value)))
            return line.getvalue().encode("utf-8")

        yield b"kind,payloadJson\r\n"
        yield row("manifest", manifest)
        for index, block in enumerate(_partition(summary, 32768)):
            yield row(
                "result-part",
                {
                    "index": index,
                    "encoding": "base64-utf8",
                    "bytes": base64.b64encode(block).decode("ascii"),
                },
            )
        for record in records(proof, status):
            yield row("record", record)
        for dictionary in dictionaries(proof, status):
            for index, block in enumerate(_partition(dictionary, 32768)):
                yield row(
                    "groups-part",
                    {
                        "responseId": dictionary["responseId"],
                        "index": index,
                        "encoding": "base64-utf8",
                        "bytes": base64.b64encode(block).decode("ascii"),
                    },
                )
    else:
        # No links, scripts, styling, foreignObjects or externally resolved names.
        yield b'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="120" viewBox="0 0 640 120"><title>Evidence export</title><desc>Exact records and analysis in metadata</desc><metadata>'
        yield escape(encode(manifest, ascii_only=True)).encode("ascii") + b"\n"
        for block in _partition(summary, 8192, ascii_only=True):
            # escape() on decoded blocks requires preserving UTF-8 boundaries.
            yield (
                block.replace(b"&", b"&amp;")
                .replace(b"<", b"&lt;")
                .replace(b">", b"&gt;")
            )
        yield b"\n"
        for record in records(proof, status):
            yield (
                escape(encode({"record": record}, ascii_only=True)).encode("ascii")
                + b"\n"
            )
        for dictionary in dictionaries(proof, status):
            for block in _partition(
                {"groupDictionary": dictionary}, 8192, ascii_only=True
            ):
                yield (
                    block.replace(b"&", b"&amp;")
                    .replace(b"<", b"&lt;")
                    .replace(b">", b"&gt;")
                )
            yield b"\n"
        yield b'</metadata><text x="24" y="52">Evidence export</text><text x="24" y="86">Version and cohort data are available in the metadata.</text></svg>\n'
