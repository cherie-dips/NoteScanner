import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "../config";
import { authFetch, apiErrorMessage, errorText } from "../auth";
import { registerLocalFile } from "../localFileStore";
import { getNotesMirrorRoot, mirrorUploadedBytes } from "../localDiskFolder";
import { sanitizeVirtualPath } from "../virtualPath";

const POLL_MS = 2000;
const ACTIVE_STATUSES = new Set(["queued", "processing"]);

/**
 * Upload queue for the explorer. POST /upload_note returns a job id right away; the server reads the
 * file in the background, so each job is polled until it is "done" or "failed".
 *
 * Item: { key, jobId, name, path, status: uploading|queued|processing|done|failed, error,
 *         deckStatus?: making|done|error, deckMessage? }  (deck* = "Make flashcards" after upload)
 * onJobDone(job, key) runs when an upload finishes.
 */
export default function useUploadQueue({ maxUploadMb = null, onJobDone } = {}) {
  const [items, setItems] = useState([]);
  // key -> { file, mirrorRoot }: the original File is kept until its job is done.
  const filesRef = useRef(new Map());
  const itemsRef = useRef(items);
  const onJobDoneRef = useRef(onJobDone);
  const pollingRef = useRef(false);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  useEffect(() => {
    onJobDoneRef.current = onJobDone;
  }, [onJobDone]);

  const updateItem = useCallback((key, patch) => {
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));
  }, []);

  // After a page reload, show uploads the server is still working on.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`${API_BASE}/jobs`);
        if (!res.ok) return;
        const data = await res.json().catch(() => ({}));
        const active = (data.jobs || []).filter((j) => ACTIVE_STATUSES.has(j.status));
        if (cancelled || !active.length) return;
        setItems((prev) => {
          const known = new Set(prev.map((it) => it.jobId));
          const restored = active
            .filter((j) => !known.has(j.job_id))
            .map((j) => ({
              key: `job-${j.job_id}`,
              jobId: j.job_id,
              name: j.name || j.path || "file",
              path: j.path || "",
              status: j.status,
              error: null,
            }));
          return [...prev, ...restored];
        });
      } catch {
        /* the list is a convenience; ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const finishJob = useCallback(
    async (key, job) => {
      const entry = filesRef.current.get(key);
      filesRef.current.delete(key);
      const rel = sanitizeVirtualPath(job.path || "");
      if (entry && rel) {
        registerLocalFile(rel, entry.file);
        try {
          await mirrorUploadedBytes(entry.file, rel, entry.mirrorRoot);
        } catch (mirrorErr) {
          console.error("Local mirror write failed:", mirrorErr);
        }
      }
      onJobDoneRef.current?.(job, key);
    },
    [],
  );

  const pollOnce = useCallback(async () => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    try {
      const active = itemsRef.current.filter((it) => it.jobId && ACTIVE_STATUSES.has(it.status));
      if (!active.length) return;
      // One request for all uploads (not one per file), so many uploads stay within the request limit.
      let jobsById;
      try {
        const res = await authFetch(`${API_BASE}/jobs`);
        if (!res.ok) return; // temporary problem (or rate limited): try again next round
        const data = await res.json().catch(() => ({}));
        jobsById = new Map((data.jobs || []).map((j) => [j.job_id, j]));
      } catch {
        return; // network blip: try again next round
      }
      for (const it of active) {
        try {
          const data = jobsById.get(it.jobId);
          if (!data) {
            filesRef.current.delete(it.key);
            updateItem(it.key, {
              status: "failed",
              error: "The server lost track of this upload (it may have restarted). Please upload it again.",
            });
            continue;
          }
          if (data.status === it.status) continue;
          updateItem(it.key, {
            status: data.status,
            path: data.path || it.path,
            error: data.status === "failed" ? data.error || "Processing failed." : null,
          });
          if (data.status === "done") await finishJob(it.key, data);
          if (data.status === "failed") filesRef.current.delete(it.key);
        } catch {
          /* network blip: try again next round */
        }
      }
    } finally {
      pollingRef.current = false;
    }
  }, [finishJob, updateItem]);

  const hasActiveJobs = items.some((it) => it.jobId && ACTIVE_STATUSES.has(it.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setInterval(pollOnce, POLL_MS);
    return () => clearInterval(timer);
  }, [hasActiveJobs, pollOnce]);

  const uploadFiles = useCallback(
    async (fileList, folderPath = "") => {
      const files = Array.from(fileList || []).filter(Boolean);
      if (!files.length) return;
      // Ask for folder access while the click/drop still counts as a user action.
      let mirrorRoot = null;
      try {
        ({ root: mirrorRoot } = await getNotesMirrorRoot());
      } catch {
        mirrorRoot = null;
      }
      const stamp = Date.now();
      const batch = files.map((file, i) => ({
        key: `up-${stamp}-${i}-${file.name}`,
        jobId: null,
        name: file.name,
        path: "",
        status: "uploading",
        error: null,
      }));
      batch.forEach((b, i) => filesRef.current.set(b.key, { file: files[i], mirrorRoot }));
      setItems((prev) => [...batch, ...prev]);

      // Send one at a time: the server only queues the work, so each request is quick.
      for (let i = 0; i < files.length; i++) {
        const { key } = batch[i];
        const file = files[i];
        try {
          if (maxUploadMb && file.size > maxUploadMb * 1024 * 1024) {
            throw new Error(`File is too large. The limit is ${maxUploadMb} MB.`);
          }
          const fd = new FormData();
          fd.append("path", sanitizeVirtualPath(folderPath || ""));
          fd.append("file", file);
          const res = await authFetch(`${API_BASE}/upload_note`, { method: "POST", body: fd });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(apiErrorMessage(data, res));
          const status = data.status || "queued";
          updateItem(key, {
            jobId: data.job_id,
            path: data.path || "",
            status,
            error: status === "failed" ? data.error || "Processing failed." : null,
          });
          if (status === "done") await finishJob(key, data);
        } catch (err) {
          filesRef.current.delete(key);
          updateItem(key, { status: "failed", error: errorText(err) });
        }
      }
    },
    [finishJob, maxUploadMb, updateItem],
  );

  const dismiss = useCallback((key) => {
    filesRef.current.delete(key);
    setItems((prev) => prev.filter((it) => it.key !== key));
  }, []);

  const clearFinished = useCallback(() => {
    setItems((prev) => prev.filter((it) => it.status !== "done" && it.status !== "failed"));
  }, []);

  return { items, uploadFiles, dismiss, clearFinished, updateItem };
}
