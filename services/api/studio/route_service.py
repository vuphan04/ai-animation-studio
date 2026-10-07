"""Workflow V4.5 step 05 and checkpoint C3: route per shot, expected cost and budget gate 1.

Nothing here builds a shot or sends a paid request. C3 records which route the operator locked for every
shot of the episode; the routes are executed in later steps.
"""

from __future__ import annotations

import json

from . import budget, workflow
from .breakdown_domain import entity_index, resolve
from .domain import DomainError, canonical, digest
from .route_domain import (EMPTY_PRICES, MODEL_LABELS, MODEL_LIMITS, ROUTES, SUGGESTED_PRICES, clean_prices, review, suggest)
from .series_domain import VIDEO_MODELS, fold
from .service import now, require


class RouteService:
    def __init__(self, studio, breakdown):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown

    # ---- series-wide settings: main characters and price table ---------------------------------------------------

    @staticmethod
    def _settings(conn, project_id: str) -> dict:
        row = conn.execute("SELECT * FROM route_settings WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return {"revision": 0, "main_characters": [], "prices": EMPTY_PRICES, "saved": False}
        return {"revision": row["revision"], "main_characters": json.loads(row["main_characters_json"]),
                "prices": json.loads(row["prices_json"]), "saved": True}

    def settings(self, project_id: str) -> dict:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            value = self._settings(conn, project_id)
            characters = [{"entity_key": r["entity_key"], "display_name": r["display_name"]} for r in conn.execute(
                "SELECT entity_key,display_name FROM series_entities WHERE project_id=? AND kind='character' AND active=1 ORDER BY display_name", (project_id,))]
        return {**value, "characters": characters, "suggested_prices": SUGGESTED_PRICES,
                "models": [{"id": model, "label": MODEL_LABELS[model], **MODEL_LIMITS[model]} for model in VIDEO_MODELS],
                "price_note": "Giá do bạn nhập theo bảng giá tài khoản; app không tự lấy giá từ nhà cung cấp."}

    def save_settings(self, project_id: str, expected_revision: int, main_characters: list, prices: dict) -> dict:
        self.studio.project(project_id)
        clean = clean_prices(prices)
        with self.db.transaction() as conn:
            if self._settings(conn, project_id)["revision"] != expected_revision:
                raise DomainError("Bảng giá hoặc danh sách nhân vật chính đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            known = {r[0] for r in conn.execute("SELECT entity_key FROM series_entities WHERE project_id=? AND kind='character' AND active=1", (project_id,))}
            if not isinstance(main_characters, list) or len(set(main_characters)) != len(main_characters) or any(key not in known for key in main_characters):
                raise DomainError("Nhân vật chính phải chọn từ danh sách nhân vật của thư viện series.", 409)
            conn.execute("INSERT OR REPLACE INTO route_settings(project_id,revision,main_characters_json,prices_json,updated_at) VALUES(?,?,?,?,?)",
                         (project_id, expected_revision + 1, canonical(sorted(main_characters)), canonical(clean), now()))
            self.studio.audit(conn, project_id, "route.settings_saved", project_id, {"revision": expected_revision + 1})
        return self.settings(project_id)

    # ---- plan ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _checkpoint(conn, episode: dict, key: str):
        return conn.execute("SELECT * FROM workflow_checkpoints WHERE project_id=? AND checkpoint=? AND scope_id=? ORDER BY exact_version DESC LIMIT 1",
                            (episode["project_id"], key, episode["id"])).fetchone()

    def _plan(self, conn, episode: dict) -> dict:
        project_id = episode["project_id"]
        data = self.breakdown._compare(conn, episode)
        comparison = self.breakdown._comparison(conn, episode, data)
        settings = self._settings(conn, project_id)
        standards = conn.execute("SELECT default_video_model FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
        default_model = standards["default_video_model"] if standards else VIDEO_MODELS[0]
        revision = data["revision"]
        blockers = []
        if not revision:
            blockers.append("Chưa có breakdown của tập.")
        elif not data["c1"] or not data["c1"]["current"]:
            blockers.append("C1: breakdown hiện tại chưa được chốt.")
        if revision and not (comparison["c2"] and comparison["c2"]["current"]):
            blockers.append("C2: phần bổ sung thư viện của tập chưa được duyệt.")
        base = {"episode": episode, "ready": not blockers, "blockers": blockers, "default_video_model": default_model,
                "settings_saved": settings["saved"], "scenes": [], "totals": [], "plan_hash": None, "c3": None,
                "counts": {"shots": 0, "locked": 0, "UE": 0, "AI": 0, "HYBRID": 0, "kimodo": 0},
                "approve_blockers": list(blockers), "allowed": False}
        if blockers:
            return base
        entities = {e["entity_key"]: e for e in data["library"]["state"]["entities"]}
        labels = {key: e["display_name"] for key, e in entities.items()}
        mapped = {key for key, e in entities.items() if e["mapping"] and e["mapping"]["status"] == "MAPPED"}
        index = entity_index(list(entities.values()))
        main = set(settings["main_characters"]) & set(index["character"])
        missing_tags = {kind: {} for kind in ("motions", "vfx")}
        for kind in missing_tags:
            for item in data["missing"][kind]:
                for shot_id in item["shots"]:
                    missing_tags[kind].setdefault(shot_id, []).append(item["tag"])
        decisions = {r["shot_id"]: r for r in conn.execute("SELECT * FROM route_decisions WHERE episode_id=?", (episode["id"],))}
        content = json.loads(revision["content_json"])
        scenes, hashed, sums, counts = [], [], {}, dict(base["counts"])

        def key_of(kind: str, name: str, key: str | None) -> str:
            found = resolve(index, kind, name, key)
            if not found:
                labels.setdefault("name:" + fold(name), name)
            return found or "name:" + fold(name)

        for scene in content["scenes"]:
            environment = key_of("environment", scene["environment"]["name"], scene["environment"]["entity_key"])
            shots = []
            for shot in scene["shots"]:
                shot_id = f"{episode['code']}_{scene['scene_key']}_{shot['shot_key']}"
                speakers = sorted({key_of("character", l["character"]["name"], l["character"]["entity_key"]) for l in shot["dialogue"]})
                characters = sorted({key_of("character", c["name"], c["entity_key"]) for c in shot["characters"]} | set(speakers))
                facts = {"shot_size": shot["shot_size"], "face_visible": shot["face_visible"], "content_flags": shot["content_flags"],
                         "duration_seconds": shot["duration_seconds"], "environment": environment, "characters": characters, "speakers": speakers,
                         "missing_assets": sorted(key for key in {environment, *characters} if key not in mapped),
                         # With no main character marked, every speaking character is treated as one.
                         "main_dialogue": any(key in main for key in speakers) if main else bool(speakers),
                         "missing_motion": sorted(missing_tags["motions"].get(shot_id, [])), "missing_vfx": sorted(missing_tags["vfx"].get(shot_id, [])),
                         "dialogue_chars": sum(len(l["text"]) for l in shot["dialogue"])}
                basis = digest(facts)
                suggestion = suggest(facts, labels)
                suggestion["video_model"] = default_model if suggestion["route"] != "UE" else None
                row = decisions.get(shot_id)
                route, model = (row["route"], row["video_model"]) if row else (suggestion["route"], suggestion["video_model"])
                checked = review(facts, route, model, settings["prices"], labels)
                stale = bool(row) and bool(row["locked"]) and row["basis_hash"] != basis
                locked = bool(row) and bool(row["locked"]) and not stale and not checked["issues"]
                estimate = checked["estimate"]
                for currency, amount, part in ((estimate["video_currency"], estimate["video_micro"], "video_micro"), (estimate["voice_currency"], estimate["voice_micro"], "voice_micro")):
                    if currency and amount:
                        sums.setdefault(currency, {"video_micro": 0, "voice_micro": 0})[part] += amount
                counts["shots"] += 1
                counts[route] += 1
                counts["locked"] += locked
                counts["kimodo"] += checked["needs_kimodo"]
                hashed.append([shot_id, route, model, basis, locked])
                shots.append({"shot_id": shot_id, "scene_key": scene["scene_key"], "shot_key": shot["shot_key"], "description": shot["description"],
                              "shot_size": shot["shot_size"], "duration_seconds": shot["duration_seconds"], "face_visible": shot["face_visible"],
                              "content_flags": shot["content_flags"], "has_dialogue": bool(speakers), "facts": facts, "basis_hash": basis,
                              "suggestion": suggestion, "route": route, "video_model": model, "locked": locked,
                              "decision": {"revision": row["revision"], "locked": bool(row["locked"]), "stale": stale} if row else None,
                              **checked})
            scenes.append({"scene_key": scene["scene_key"], "heading": scene["heading"], "shots": shots})
        totals = []
        for currency in sorted(sums):
            total = sums[currency]["video_micro"] + sums[currency]["voice_micro"]
            totals.append({"currency": currency, **sums[currency], "total_micro": total, "budget": budget.check(conn, project_id, currency, total)})
        plan_hash = digest({"breakdown": revision["content_hash"], "library_version": comparison["library"]["version"],
                            "settings": digest({"main": settings["main_characters"], "prices": settings["prices"]}), "shots": hashed})
        approve = []
        if not settings["saved"]:
            approve.append("Chưa lưu bảng giá và danh sách nhân vật chính của series.")
        if counts["locked"] < counts["shots"]:
            approve.append(f"Còn {counts['shots'] - counts['locked']} shot chưa khóa phương thức.")
        approve += [f"Cổng ngân sách 1 ({t['currency']}): {t['budget']['reason']}" for t in totals if not t["budget"]["allowed"]]
        latest = self._checkpoint(conn, episode, "C3")
        c3 = {"sequence": latest["exact_version"], "created_at": latest["created_at"], "decision": json.loads(latest["decision_json"]),
              "current": latest["dependency_hash"] == plan_hash} if latest else None
        return {**base, "scenes": scenes, "totals": totals, "counts": counts, "plan_hash": plan_hash, "c3": c3,
                "breakdown_version": revision["version"], "library_version": comparison["library"]["version"],
                "prices": settings["prices"], "labels": labels, "entities": entities, "approve_blockers": approve, "allowed": not approve}

    @staticmethod
    def _public(plan: dict) -> dict:
        return {**{k: v for k, v in plan.items() if k not in ("prices", "labels", "entities")},
                "scenes": [dict(scene, shots=[{k: v for k, v in shot.items() if k != "facts"} for shot in scene["shots"]]) for scene in plan["scenes"]]}

    def plan(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._plan(conn, self.breakdown._episode(conn, episode_id)))

    def c3_current(self, conn, episode: dict) -> bool:
        plan = self._plan(conn, episode)
        return bool(plan["c3"] and plan["c3"]["current"])

    # ---- decisions and C3 -------------------------------------------------------------------------------------------

    @staticmethod
    def _store(conn, episode: dict, shot: dict, route: str, model: str | None, locked: bool) -> None:
        conn.execute("INSERT INTO route_decisions(episode_id,shot_id,project_id,route,video_model,locked,basis_hash,revision,updated_at) VALUES(?,?,?,?,?,?,?,1,?) "
                     "ON CONFLICT(episode_id,shot_id) DO UPDATE SET route=excluded.route,video_model=excluded.video_model,locked=excluded.locked,basis_hash=excluded.basis_hash,revision=revision+1,updated_at=excluded.updated_at",
                     (episode["id"], shot["shot_id"], episode["project_id"], route, model, int(locked), shot["basis_hash"], now()))

    def decide(self, episode_id: str, shot_id: str, expected_revision: int, route: str, video_model: str | None, locked: bool) -> dict:
        if route not in ROUTES or type(locked) is not bool or (route != "UE" and video_model not in VIDEO_MODELS):
            raise DomainError("Chọn UE, AI hoặc Hybrid; shot AI/Hybrid cần chọn model video.")
        model = None if route == "UE" else video_model
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            plan = self._plan(conn, episode)
            if not plan["ready"]:
                raise DomainError("Chưa phân loại được: " + " ".join(plan["blockers"]), 409)
            shot = require(next((s for scene in plan["scenes"] for s in scene["shots"] if s["shot_id"] == shot_id), None), "shot")
            if (shot["decision"]["revision"] if shot["decision"] else 0) != expected_revision:
                raise DomainError("Phương thức của shot này đã đổi ở phiên khác; tải lại.", 409)
            if locked:
                issues = review(shot["facts"], route, model, plan["prices"], plan["labels"])["issues"]
                if issues:
                    raise DomainError(f"Chưa khóa được {shot_id}: " + " ".join(issues), 409)
            self._store(conn, episode, shot, route, model, locked)
            self.studio.audit(conn, episode["project_id"], "route.decided", episode_id, {"shot": shot_id, "route": route, "model": model, "locked": locked})
        return self.plan(episode_id)

    def lock_many(self, episode_id: str, expected_plan_hash: str, scene_key: str | None) -> dict:
        """Lock every shot of a scene (or of the episode) at the route it currently shows; shots with issues are left open."""
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            plan = self._plan(conn, episode)
            if not plan["ready"]:
                raise DomainError("Chưa phân loại được: " + " ".join(plan["blockers"]), 409)
            if plan["plan_hash"] != expected_plan_hash:
                raise DomainError("Bảng phân loại đã đổi sau khi bạn xem; tải lại.", 409)
            locked, skipped = 0, []
            for scene in plan["scenes"]:
                if scene_key and scene["scene_key"] != scene_key:
                    continue
                for shot in scene["shots"]:
                    if shot["locked"]:
                        continue
                    if shot["issues"]:
                        skipped.append(shot["shot_id"])
                        continue
                    self._store(conn, episode, shot, shot["route"], shot["video_model"], True)
                    locked += 1
            self.studio.audit(conn, episode["project_id"], "route.locked_many", episode_id, {"scene": scene_key, "locked": locked, "skipped": len(skipped)})
        return {**self.plan(episode_id), "bulk": {"locked": locked, "skipped": skipped}}

    def approve_c3(self, episode_id: str, expected_hash: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            plan = self._plan(conn, episode)
            if plan["approve_blockers"]:
                raise DomainError("Chưa chốt được C3: " + " · ".join(plan["approve_blockers"][:6]), 409)
            if plan["plan_hash"] != expected_hash:
                raise DomainError("Phương thức, bảng giá hoặc breakdown đã đổi sau khi bạn xem; tải lại.", 409)
            if plan["c3"] and plan["c3"]["current"]:
                raise DomainError("Bảng phân loại này đã được chốt.", 409)
            sequence = (plan["c3"]["sequence"] if plan["c3"] else 0) + 1
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C3", episode_id, sequence, expected_hash,
                                       {"breakdown_version": plan["breakdown_version"], "library_version": plan["library_version"],
                                        "routes": {route: plan["counts"][route] for route in ROUTES}, "kimodo_shots": plan["counts"]["kimodo"],
                                        "estimated": [{k: t[k] for k in ("currency", "video_micro", "voice_micro", "total_micro")} for t in plan["totals"]],
                                        "shots": [{"shot_id": s["shot_id"], "route": s["route"], "video_model": s["video_model"], "needs_kimodo": s["needs_kimodo"]}
                                                  for scene in plan["scenes"] for s in scene["shots"]]})
        return self.plan(episode_id)

    def retimed_cost(self, plan: dict, seconds_by_shot: dict) -> dict:
        """The same estimate with the shot lengths fixed at C4 instead of the breakdown's guesses."""
        sums, issues = {}, []
        for scene in plan["scenes"]:
            for shot in scene["shots"]:
                seconds = seconds_by_shot.get(shot["shot_id"])
                if shot["route"] == "UE" or seconds is None:
                    continue
                checked = review(dict(shot["facts"], duration_seconds=seconds), shot["route"], shot["video_model"], plan["prices"], plan["labels"])
                estimate = checked["estimate"]
                if estimate["video_currency"]:
                    sums[estimate["video_currency"]] = sums.get(estimate["video_currency"], 0) + estimate["video_micro"]
                if checked["over_max"]:
                    issues.append(f"{shot['shot_id']}: dài {seconds:g} giây sau khi chốt timing, vượt clip tối đa của {MODEL_LABELS[shot['video_model']]}.")
        return {"video": [{"currency": currency, "video_micro": amount} for currency, amount in sorted(sums.items())], "issues": issues}
