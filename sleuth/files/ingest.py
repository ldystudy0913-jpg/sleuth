"""Decrypt + extract session files with a process-local concurrency cap."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from ..config import Config
from .cos import CosError, CosNotConfigured, ObjectStore, object_store_from_config
from .crypto_sm4 import Sm4CbcError, sm4_cbc_decrypt
from .extract import Excerpt, extract_bytes
from .mailbox import (
    files_config,
    get_file,
    record_files,
    save_record_files,
    session_files,
    write_session_files,
)
from . import settings as file_settings


class ExtractScheduler:
    def __init__(self) -> None:
        self._sem: Optional[threading.BoundedSemaphore] = None
        self._sem_n: Optional[int] = None
        self._lock = threading.Lock()
        self._inflight: Dict[str, threading.Event] = {}
        self._session_locks: Dict[str, threading.Lock] = {}
        self._progress_hooks: Dict[str, Any] = {}
        self.active = 0
        self.max_active = 0

    def set_progress_hook(self, session_id: str, fn) -> None:
        sid = str(session_id or "")
        with self._lock:
            if fn is None:
                self._progress_hooks.pop(sid, None)
            elif sid:
                self._progress_hooks[sid] = fn

    def notify_progress(self, session_id: str, **payload: Any) -> None:
        sid = str(session_id or "")
        with self._lock:
            fn = self._progress_hooks.get(sid)
        if callable(fn):
            try:
                fn(**payload)
            except Exception:
                pass

    def _semaphore(self, n: int) -> threading.BoundedSemaphore:
        n = max(1, int(n or 1))
        if self._sem is None or self._sem_n != n:
            self._sem = threading.BoundedSemaphore(n)
            self._sem_n = n
        return self._sem

    def session_lock(self, session_id: str) -> threading.Lock:
        with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            with self._lock:
                pending = [ev for ev in self._inflight.values() if not ev.is_set()]
            if not pending:
                return True
            pending[0].wait(timeout=max(0.01, deadline - time.time()))
        with self._lock:
            return all(ev.is_set() for ev in self._inflight.values())

    def schedule(
        self,
        *,
        config: Config,
        store,
        session_id: str,
        file_id: str,
        object_store: Optional[ObjectStore] = None,
    ) -> threading.Event:
        key = f"{session_id}:{file_id}"
        with self._lock:
            ev = self._inflight.get(key)
            if ev is not None:
                return ev
            ev = threading.Event()
            self._inflight[key] = ev
        thread = threading.Thread(
            target=self._run,
            args=(config, store, session_id, file_id, object_store, ev, key),
            daemon=True,
            name=f"sleuth-extract-{file_id[:16]}",
        )
        thread.start()
        return ev

    def _run(
        self,
        config: Config,
        store,
        session_id: str,
        file_id: str,
        object_store: Optional[ObjectStore],
        ev: threading.Event,
        key: str,
    ) -> None:
        fcfg = files_config(config)
        sem = self._semaphore(int(fcfg.extract_concurrency or 2))
        timeout = float(fcfg.extract_timeout_s or 45)
        acquired = False
        try:
            acquired = sem.acquire(timeout=timeout)
            if not acquired:
                self._apply_excerpt(
                    store,
                    session_id,
                    file_id,
                    Excerpt(skipped="extract timeout waiting for slot"),
                    config,
                )
                return
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                self._extract_one(config, store, session_id, file_id, object_store)
            finally:
                with self._lock:
                    self.active = max(0, self.active - 1)
        except Exception as exc:
            self._apply_excerpt(
                store,
                session_id,
                file_id,
                Excerpt(skipped=f"extract failed: {exc}"),
                config,
            )
        finally:
            if acquired:
                sem.release()
            ev.set()
            with self._lock:
                self._inflight.pop(key, None)

    def _extract_one(
        self,
        config: Config,
        store,
        session_id: str,
        file_id: str,
        object_store: Optional[ObjectStore],
    ) -> None:
        rec = store.get_session(session_id) if store is not None else None
        if rec is None:
            return
        item = get_file(record_files(rec), file_id)
        if item is None:
            return
        if str(item.get("excerpt_status") or "") in file_settings.excerpt_done(config):
            return
        excerpt = extract_item(
            config=config,
            item=item,
            object_store=object_store,
            on_progress=lambda **kw: self.notify_progress(session_id, **kw),
        )
        self._apply_excerpt(store, session_id, file_id, excerpt, config)

    def _apply_excerpt(self, store, session_id: str, file_id: str, excerpt: Excerpt, config=None) -> None:
        if store is None:
            return
        try:
            with self.session_lock(session_id):
                rec = store.get_session(session_id)
                if rec is None:
                    return
                files = record_files(rec)
                item = get_file(files, file_id)
                if item is None:
                    return
                write_excerpt_fields(item, excerpt, config)
                save_record_files(store, rec, files)
        except Exception:
            return


_SCHEDULER: Optional[ExtractScheduler] = None
_SCHEDULER_LOCK = threading.Lock()


def scheduler() -> ExtractScheduler:
    global _SCHEDULER
    with _SCHEDULER_LOCK:
        if _SCHEDULER is None:
            _SCHEDULER = ExtractScheduler()
        return _SCHEDULER


def reset_scheduler() -> ExtractScheduler:
    global _SCHEDULER
    with _SCHEDULER_LOCK:
        old = _SCHEDULER
        _SCHEDULER = ExtractScheduler()
    if old is not None:
        old.wait_idle(2.0)
    return _SCHEDULER


def wait_extracts(timeout: float = 5.0) -> bool:
    return scheduler().wait_idle(timeout)


def write_excerpt_fields(item: Dict[str, Any], excerpt: Excerpt, config=None) -> None:
    payload: Dict[str, Any] = {
        "text": excerpt.text or "",
        "truncated": bool(excerpt.truncated),
        "parser": excerpt.parser or "",
    }
    if excerpt.skipped:
        payload["skipped"] = excerpt.skipped
        item["excerpt_status"] = file_settings.excerpt_skipped(config)
    else:
        item["excerpt_status"] = file_settings.excerpt_ok(config)
    item["excerpt"] = payload


def extract_item(
    *,
    config: Config,
    item: Dict[str, Any],
    object_store: Optional[ObjectStore] = None,
    max_chars: int = 0,
    vision_prompt: Optional[str] = None,
    on_progress=None,
) -> Excerpt:
    fcfg = files_config(config)
    key = str(item.get("object_key") or "")
    if not key:
        return Excerpt(skipped="file is missing object_key")
    store_impl = object_store
    if store_impl is None:
        try:
            store_impl = object_store_from_config(config)
        except CosNotConfigured as exc:
            return Excerpt(skipped=str(exc))
    raw = b""
    data = b""
    try:
        raw = store_impl.get_bytes(key, max_bytes=int(fcfg.max_bytes or 0))
        if item.get("encrypted"):
            sm4_key = (fcfg.sm4_key or "").strip()
            if not sm4_key:
                return Excerpt(skipped=file_settings.err_sm4_key(config))
            data = sm4_cbc_decrypt(raw, sm4_key)
        else:
            data = raw
        return extract_bytes(
            data,
            mime=str(item.get("mime") or ""),
            filename=str(item.get("filename") or ""),
            config=config,
            max_chars=max_chars,
            vision_prompt=vision_prompt,
            on_progress=on_progress,
            file_id=str(item.get("id") or ""),
        )
    except Sm4CbcError as exc:
        return Excerpt(skipped=f"sm4 decrypt failed: {exc}")
    except CosError as exc:
        return Excerpt(skipped=str(exc))
    except Exception as exc:
        return Excerpt(skipped=f"extract failed: {exc}")
    finally:
        del raw
        del data


def schedule_extract(
    *,
    config: Config,
    store,
    session_id: str,
    file_id: str,
    object_store: Optional[ObjectStore] = None,
) -> threading.Event:
    return scheduler().schedule(
        config=config,
        store=store,
        session_id=session_id,
        file_id=file_id,
        object_store=object_store,
    )


def _needs_extract(item: Dict[str, Any], config=None) -> bool:
    if str(item.get("status") or "") != file_settings.status_ready(config):
        return False
    pending = file_settings.excerpt_pending(config)
    return str(item.get("excerpt_status") or pending) not in file_settings.excerpt_done(config)


def ensure_session_excerpts(
    session,
    *,
    timeout_s: float = 8.0,
    object_store: Optional[ObjectStore] = None,
    progress_target=None,
) -> None:
    config = getattr(session, "config", None) or Config()
    files = session_files(session)
    pending = [f for f in files if _needs_extract(f, config)]
    if not pending:
        return
    from ..progress import emit_progress

    target = progress_target if progress_target is not None else session
    fcfg = files_config(config)
    store_impl = object_store or getattr(session, "_object_store", None)
    store = getattr(session, "store", None)
    sid = str(getattr(session, "id", "") or "")

    def _hook(**payload):
        emit_progress(target, **payload)

    emit_progress(
        target,
        stage=fcfg.stage_extract or "extract",
        detail=fcfg.progress_detail_extract or "extracting attachments",
    )
    if store is not None and sid:
        sched = scheduler()
        sched.set_progress_hook(sid, _hook)
        events = []
        try:
            for item in pending:
                events.append(
                    schedule_extract(
                        config=config,
                        store=store,
                        session_id=sid,
                        file_id=str(item.get("id") or ""),
                        object_store=store_impl,
                    )
                )
            deadline = time.time() + max(0.0, float(timeout_s))
            interval = float(getattr(fcfg, "progress_interval_s", 1.0) or 1.0)
            for ev in events:
                while not ev.is_set() and time.time() < deadline:
                    emit_progress(
                        target,
                        stage=fcfg.stage_extract_wait or "extract_wait",
                        detail=fcfg.progress_detail_wait or "waiting for excerpt",
                    )
                    ev.wait(timeout=min(interval, max(0.0, deadline - time.time())))
        finally:
            sched.set_progress_hook(sid, None)
        emit_progress(
            target,
            stage=fcfg.stage_extract_done or "extract_done",
            detail=fcfg.progress_detail_done or "excerpt ready",
        )
        return
    for item in pending:
        excerpt = extract_item(
            config=config,
            item=item,
            object_store=store_impl,
            on_progress=_hook,
        )
        write_excerpt_fields(item, excerpt, config)
    write_session_files(session, files)
    emit_progress(
        target,
        stage=fcfg.stage_extract_done or "extract_done",
        detail=fcfg.progress_detail_done or "excerpt ready",
    )
