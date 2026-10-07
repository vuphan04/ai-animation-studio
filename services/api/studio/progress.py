"""Where a project and one of its episodes stand in workflow V4.5: every checkpoint from CS1 to C10.

Nothing is stored here. Each state is asked from the service that owns the checkpoint, so the map says
what those screens say. The interface uses it to show what is done and what comes next.
"""

from __future__ import annotations

from . import workflow
from .domain import DomainError

# Zone of the diagram, its screen, and the checkpoints approved there.
ZONES = (("0", "Thư viện series", "series", ("CS1", "CS2")),
         ("A", "Tập phim", "episode", ("C1", "C2", "C3", "C4")),
         ("B", "Dựng shot", "production", ("C5", "C6", "C7")),
         ("C", "Final & xuất phim", "finals", ("C8", "C9", "C10")))
# The step of the screen where each checkpoint is approved.
STEPS = {"CS1": "mapping", "CS2": "style", "C1": "breakdown", "C2": "library", "C3": "route", "C4": "timing",
         "C5": "ue", "C6": "proxy", "C7": "rough", "C8": "final", "C9": "sound", "C10": "delivery"}
DONE, STALE, PARTIAL, TODO = "DONE", "STALE", "PARTIAL", "TODO"


def _versioned(item: dict | None) -> tuple[str, str]:
    if not item or not item["version"]:
        return TODO, ""
    return (STALE, f"bản {item['version']} đang dùng, bản nháp có thay đổi") if item["draft_changed"] else (DONE, f"bản {item['version']}")


def _approved(checkpoint: dict | None, done: str, pending: str = "") -> tuple[str, str]:
    """State of a checkpoint approved once for the episode. The interface names the state; the detail adds what it does not say."""
    if not checkpoint:
        return TODO, pending
    return (DONE, done) if checkpoint["current"] else (STALE, pending)


def _counted(parts: list[tuple[str, int, int]]) -> tuple[str, str]:
    """Checkpoints approved shot by shot: (branch, approved, total) per branch."""
    parts = [part for part in parts if part[2]]
    done, total = sum(part[1] for part in parts), sum(part[2] for part in parts)
    detail = " · ".join(f"{name} {count}/{size}" for name, count, size in parts) or "không có shot"
    return (DONE if total and done == total else PARTIAL if done else TODO), detail


class WorkflowProgress:
    def __init__(self, studio, series, style, breakdown, timing, final, sound, delivery):
        self.studio = studio
        self.db = studio.db
        self.series, self.style, self.breakdown, self.timing = series, style, breakdown, timing
        self.final, self.sound, self.delivery = final, sound, delivery

    def state(self, project_id: str, episode_id: str | None = None) -> dict:
        self.studio.project(project_id)
        found: dict[str, tuple[str, str]] = {}
        with self.db.connect() as conn:
            found["CS1"] = _versioned(self.series.library_state(conn, project_id))
            found["CS2"] = _versioned(self.style.bible_state(conn, project_id))
            if episode_id:
                episode = self.breakdown._episode(conn, episode_id)
                if episode["project_id"] != project_id:
                    raise DomainError("Tập này không thuộc dự án đang chọn.", 409)
                found.update(self._episode(conn, episode))
        zones, order = [], []
        for key, label, page, checkpoints in ZONES:
            items = [{"key": name, "label": workflow.BY_KEY[name]["label"], "page": page, "step": STEPS[name],
                      "state": found[name][0], "detail": found[name][1]} for name in checkpoints if name in found]
            zones.append({"key": key, "label": label, "page": page, "checkpoints": items})
            order += items
        return {"episode_id": episode_id, "zones": zones,
                "next": next((item for item in order if item["state"] != DONE), None),
                "done": sum(item["state"] == DONE for item in order), "total": len(order)}

    def _episode(self, conn, episode: dict) -> dict[str, tuple[str, str]]:
        compared = self.breakdown._compare(conn, episode)
        revision = compared["revision"]
        found = {"C1": _approved(compared["c1"], f"breakdown bản {revision['version']}" if revision else "", f"breakdown bản {revision['version']} chờ chốt" if revision else "chưa có breakdown"),
                 "C2": _approved(self.breakdown._comparison(conn, episode, compared)["c2"], "thư viện đủ cho tập")}
        timing = self.timing._state(conn, episode)
        plan = timing["_inputs"]["plan"]
        counts = plan["counts"]
        found["C3"] = _approved(plan["c3"], f"UE {counts['UE']} · AI {counts['AI']} · Hybrid {counts['HYBRID']}",
                                f"đã khóa {counts['locked']}/{counts['shots']} shot" if counts["shots"] else "")
        found["C4"] = _approved(timing["c4"], f"{timing['summary']['shots']} shot · {timing['summary']['duration_seconds']:g} giây")
        finals = self.final._state(conn, episode)
        if not finals["ready"]:
            waiting = (TODO, "chờ C3 và C4")
            return {**found, **{key: waiting for key in ("C5", "C6", "C7", "C8", "C9", "C10")}}
        rough = finals["_rough"]
        ue, ai = list(rough["_ue"].values()), list(rough["_ai"].values())
        found["C5"] = _counted([("UE", sum(shot["c5_locked"] for shot in ue), len(ue)),
                                ("AI", sum(any(take["current"] and take["review"] == "APPROVED" for take in shot["takes"]) for shot in ai), len(ai))])
        direct = [shot for shot in ue if shot["route"] == "UE"]
        found["C6"] = _counted([("UE", sum(shot["c6"]["locked"] for shot in direct), len(direct)), ("AI", sum(shot["c6"]["locked"] for shot in ai), len(ai))])
        scenes = rough["summary"]
        found["C7"] = (_approved(rough["c7"], f"{scenes['scenes']} scene đã khóa dựng") if rough["c7"]
                       else (PARTIAL if scenes["locked"] else TODO, f"{scenes['locked']}/{scenes['scenes']} scene đã khóa"))
        found["C8"] = _counted([("shot", finals["summary"]["approved"], finals["summary"]["shots"])])
        sound = self.sound._state(conn, episode, rough)
        found["C9"] = _approved(sound["c9"], "âm thanh đã duyệt", "đã ghép stem, chờ nghe và duyệt" if sound["build"] and sound["build"]["current"] else "")
        delivery = self.delivery._state(conn, episode, finals, sound)
        package = delivery["package"]
        found["C10"] = _approved(delivery["c10"], "đã xuất phim", f"gói bản {package['version']}, chờ xuất phim" if package and package["current"] else "chưa có gói bàn giao")
        if found["C9"][0] == TODO and sound["build"] and sound["build"]["current"]:
            found["C9"] = (PARTIAL, found["C9"][1])
        if found["C10"][0] == TODO and package and package["current"]:
            found["C10"] = (PARTIAL, found["C10"][1])
        return found
