#!/usr/bin/env python3
"""Wait for the Kev cmdrisk fine-tune, then score → Qwen LoRA → score → light distill.

    python3 scripts/chain_after_kev.py

Logs to data/chain.log. Skips stages whose marker files already exist so a
restart is safe. Never touches the published test split.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KEV = Path("/Users/paul/Development/Hackathons/kev")
DATA = REPO / "data"
RESULTS = REPO / "results"
RUNS = REPO / "runs"
KEV_OUT = KEV / "runs" / "cmdrisk-0.8b"
QWEN_OUT = RUNS / "cmdrisk-qwen06"
LOG = DATA / "chain.log"
KEV_TRAIN_LOG = DATA / "kev_train.log"

KEV_PORT = 8010
QWEN_UPSTREAM = 8080
QWEN_BRIDGE = 8011


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def kev_train_running() -> bool:
    try:
        out = subprocess.check_output(["pgrep", "-fl", "kev.train"], text=True)
    except subprocess.CalledProcessError:
        return False
    return "cmdrisk-0.8b" in out


def kev_saved() -> bool:
    # kev.train prints "saved <out>" and writes adapter_config / head weights at the end
    if (KEV_OUT / "adapter_config.json").exists() or (KEV_OUT / "adapter_model.safetensors").exists():
        return True
    if (KEV_OUT / "head.pt").exists():
        return True
    if KEV_TRAIN_LOG.exists() and "saved " in KEV_TRAIN_LOG.read_text(encoding="utf-8", errors="ignore"):
        return True
    return False


def wait_for_kev(poll: float = 30.0) -> None:
    log("waiting for Kev train (cmdrisk-0.8b) to finish…")
    while True:
        running = kev_train_running()
        saved = kev_saved()
        if not running and saved:
            log("Kev train finished; checkpoint present")
            return
        if not running and not saved:
            log("WARNING: kev.train not running and no checkpoint yet — waiting anyway")
        time.sleep(poll)


def http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def wait_http(url: str, timeout: float = 900.0) -> None:
    start = time.time()
    last_log = 0.0
    while time.time() - start < timeout:
        if http_ok(url):
            log(f"ready: {url}")
            return
        if time.time() - last_log > 60:
            log(f"still waiting for {url} ({int(time.time() - start)}s)…")
            last_log = time.time()
        time.sleep(2)
    raise TimeoutError(f"server not ready: {url}")


def start_proc(cmd: list[str], cwd: Path, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"start: {' '.join(cmd)}  (> {log_path})")
    fh = log_path.open("a", encoding="utf-8")
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)


def score(endpoint: str, out_dir: Path, name: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / "report.json"
    if marker.exists():
        log(f"skip score {name}: {marker} exists")
        return json.loads(marker.read_text())
    run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "cmdgate.score_api",
            "--endpoint",
            endpoint,
            "--suite",
            str(DATA / "cmdrisk_validation.jsonl"),
            "--out",
            str(out_dir),
            "--timeout",
            "120",
        ],
        cwd=REPO,
    )
    return json.loads(marker.read_text())


def automation(report: dict) -> float:
    return float(report.get("overall", {}).get("automation_rate_at_5pct", 0.0))


def severe_recall(report: dict) -> float:
    return float(report.get("cmdgate", {}).get("severe_recall_at_1pct_fpr", 0.0))


def patch_report_md(kev_r: dict, qwen_r: dict, kd_r: dict | None) -> None:
    path = REPO / "REPORT.md"
    text = path.read_text(encoding="utf-8")
    marker = "<!-- chain_after_kev:results -->"
    block_lines = [
        marker,
        "",
        "### Fine-tuned models (validation, chain)",
        "",
        "| | Accuracy | Automated at 5% errors | Recall high/critical @ 1% FPR |",
        "|---|---|---|---|",
    ]

    def row(name: str, r: dict) -> str:
        o = r.get("overall", {})
        return (
            f"| {name} | {o.get('accuracy', float('nan')):.3f} | "
            f"{100 * o.get('automation_rate_at_5pct', 0):.1f}% | "
            f"{severe_recall(r):.3f} |"
        )

    block_lines.append(row("Kev-0.8B fine-tune", kev_r))
    block_lines.append(row("Qwen3-0.6B LoRA", qwen_r))
    if kd_r is not None:
        block_lines.append(row("Short distill (kept)", kd_r))
    block_lines.append("")
    block = "\n".join(block_lines)
    if marker in text:
        # replace from marker through next blank-line-separated section heading or EOF
        pre, rest = text.split(marker, 1)
        # drop old block until a following ## heading or end
        idx = rest.find("\n## ")
        text = pre + block + (rest[idx:] if idx >= 0 else "\n")
    else:
        # insert after the results table
        needle = "| Character n-grams + logistic regression |"
        if needle in text:
            # append after that table block
            pos = text.find(needle)
            end = text.find("\n```", pos)
            if end < 0:
                end = text.find("\n## ", pos)
            if end < 0:
                text = text + "\n" + block + "\n"
            else:
                text = text[:end] + "\n" + block + "\n" + text[end:]
        else:
            text = text + "\n" + block + "\n"
    path.write_text(text, encoding="utf-8")
    log(f"updated {path}")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    log("=== chain_after_kev start ===")

    wait_for_kev()

    # --- score Kev ---
    kev_report_dir = RESULTS / "kev08_validation"
    kev_proc = None
    kev_models = f"http://127.0.0.1:{KEV_PORT}/v1/models"
    try:
        if not (kev_report_dir / "report.json").exists():
            if http_ok(kev_models):
                log(f"reusing existing Kev server on {KEV_PORT}")
            else:
                kev_proc = start_proc(
                    [
                        "uv",
                        "run",
                        "--extra",
                        "serve",
                        "python",
                        "-m",
                        "kev.serve",
                        "--run",
                        str(KEV_OUT),
                        "--port",
                        str(KEV_PORT),
                        "--device",
                        "mps",
                    ],
                    cwd=KEV,
                    log_path=DATA / "kev_serve.log",
                )
                wait_http(kev_models, timeout=900)
                time.sleep(2)
            log("scoring Kev on validation…")
        kev_report = score(
            f"http://127.0.0.1:{KEV_PORT}/v1/systemone",
            kev_report_dir,
            "kev",
        )
        log(
            f"Kev val auto@5%={automation(kev_report):.4f} "
            f"acc={kev_report['overall']['accuracy']:.4f} "
            f"severe={severe_recall(kev_report):.4f}"
        )
    finally:
        stop_proc(kev_proc)
        time.sleep(5)  # let MPS free

    # --- Qwen LoRA (Kev venv has torch / transformers / peft) ---
    if not (QWEN_OUT / "merged").exists():
        run(
            [
                str(KEV / ".venv" / "bin" / "python"),
                str(REPO / "cmdgate" / "train_qwen_lora.py"),
                "--data",
                str(DATA / "cmdrisk_qwen_train.jsonl"),
                "--out",
                str(QWEN_OUT),
                "--device",
                "mps",
                "--early-stop",
                "--val-frac",
                "0.05",
                "--patience",
                "3",
                "--epochs",
                "1",
            ],
            cwd=KEV,
        )
    else:
        log(f"skip Qwen LoRA: {QWEN_OUT / 'merged'} exists")

    # --- score Qwen via HF logprobs + bridge ---
    qwen_report_dir = RESULTS / "qwen06_validation"
    up_proc = None
    br_proc = None
    try:
        if not (qwen_report_dir / "report.json").exists():
            up_proc = start_proc(
                [
                    str(KEV / ".venv" / "bin" / "python"),
                    str(REPO / "scripts" / "hf_logprobs_server.py"),
                    "--model",
                    str(QWEN_OUT / "merged"),
                    "--port",
                    str(QWEN_UPSTREAM),
                    "--device",
                    "mps",
                ],
                cwd=KEV,
                log_path=DATA / "qwen_upstream.log",
            )
            wait_http(f"http://127.0.0.1:{QWEN_UPSTREAM}/health", timeout=900)
            br_proc = start_proc(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "thinkfast.bridge",
                    "--upstream",
                    f"http://127.0.0.1:{QWEN_UPSTREAM}",
                    "--model",
                    str(QWEN_OUT / "merged"),
                    "--port",
                    str(QWEN_BRIDGE),
                ],
                cwd=REPO,
                log_path=DATA / "qwen_bridge.log",
            )
            wait_http(f"http://127.0.0.1:{QWEN_BRIDGE}/health", timeout=120)
        qwen_report = score(
            f"http://127.0.0.1:{QWEN_BRIDGE}/v1/systemone",
            qwen_report_dir,
            "qwen",
        )
        log(
            f"Qwen val auto@5%={automation(qwen_report):.4f} "
            f"acc={qwen_report['overall']['accuracy']:.4f} "
            f"severe={severe_recall(qwen_report):.4f}"
        )
    finally:
        stop_proc(br_proc)
        stop_proc(up_proc)
        time.sleep(5)

    # --- light distill: better → other ---
    kev_auto, qwen_auto = automation(kev_report), automation(qwen_report)
    if kev_auto > qwen_auto or (
        kev_auto == qwen_auto and severe_recall(kev_report) >= severe_recall(qwen_report)
    ):
        teacher, student = "kev", "qwen"
        teacher_endpoint = f"http://127.0.0.1:{KEV_PORT}/v1/systemone"
    else:
        teacher, student = "qwen", "kev"
        teacher_endpoint = f"http://127.0.0.1:{QWEN_BRIDGE}/v1/systemone"
    log(f"distill teacher={teacher} (auto={max(kev_auto, qwen_auto):.4f}) → student={student}")

    kd_report = None
    kd_dir = RESULTS / "distill_validation"
    if (kd_dir / "report.json").exists():
        kd_report = json.loads((kd_dir / "report.json").read_text())
        log("skip distill: report exists")
    elif student == "qwen":
        # need teacher (Kev) served while collecting soft labels / training student
        kev_proc = start_proc(
            [
                "uv",
                "run",
                "--extra",
                "serve",
                "python",
                "-m",
                "kev.serve",
                "--run",
                str(KEV_OUT),
                "--port",
                str(KEV_PORT),
                "--device",
                "mps",
            ],
            cwd=KEV,
            log_path=DATA / "kev_serve_kd.log",
        )
        try:
            wait_http(f"http://127.0.0.1:{KEV_PORT}/v1/models", timeout=900)
            kd_out = RUNS / "cmdrisk-qwen06-kd"
            if not (kd_out / "merged").exists():
                run(
                    [
                        str(KEV / ".venv" / "bin" / "python"),
                        str(REPO / "cmdgate" / "distill_letters.py"),
                        "--data",
                        str(DATA / "cmdrisk_qwen_train.jsonl"),
                        "--student",
                        str(QWEN_OUT / "merged"),
                        "--teacher-endpoint",
                        teacher_endpoint,
                        "--steps",
                        "400",
                        "--out",
                        str(kd_out),
                    ],
                    cwd=KEV,
                )
        finally:
            stop_proc(kev_proc)
            time.sleep(5)

        up_proc = start_proc(
            [
                str(KEV / ".venv" / "bin" / "python"),
                str(REPO / "scripts" / "hf_logprobs_server.py"),
                "--model",
                str(RUNS / "cmdrisk-qwen06-kd" / "merged"),
                "--port",
                str(QWEN_UPSTREAM),
                "--device",
                "mps",
            ],
            cwd=KEV,
            log_path=DATA / "qwen_kd_upstream.log",
        )
        br_proc = None
        try:
            wait_http(f"http://127.0.0.1:{QWEN_UPSTREAM}/health", timeout=900)
            br_proc = start_proc(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "thinkfast.bridge",
                    "--upstream",
                    f"http://127.0.0.1:{QWEN_UPSTREAM}",
                    "--model",
                    "cmdrisk-qwen06-kd",
                    "--port",
                    str(QWEN_BRIDGE),
                ],
                cwd=REPO,
                log_path=DATA / "qwen_kd_bridge.log",
            )
            wait_http(f"http://127.0.0.1:{QWEN_BRIDGE}/health", timeout=120)
            kd_report = score(
                f"http://127.0.0.1:{QWEN_BRIDGE}/v1/systemone",
                kd_dir,
                "distill",
            )
        finally:
            stop_proc(br_proc)
            stop_proc(up_proc)

        if automation(kd_report) <= qwen_auto:
            log(
                f"distill did not improve automation ({automation(kd_report):.4f} <= {qwen_auto:.4f}); "
                "keeping pre-distill Qwen weights as the Qwen line"
            )
            # leave kd report for the record but don't treat as kept
            kept = None
        else:
            log("distill improved validation automation; keeping kd weights")
            kept = kd_report
        kd_report = kept
    else:
        # student is Kev: write soft-target JSONL from Qwen teacher, short kev.train
        log("Kev-as-student distill: collecting soft labels from Qwen bridge…")
        up_proc = start_proc(
            [
                str(KEV / ".venv" / "bin" / "python"),
                str(REPO / "scripts" / "hf_logprobs_server.py"),
                "--model",
                str(QWEN_OUT / "merged"),
                "--port",
                str(QWEN_UPSTREAM),
                "--device",
                "mps",
            ],
            cwd=KEV,
            log_path=DATA / "qwen_teacher_upstream.log",
        )
        br_proc = None
        soft_path = DATA / "cmdrisk_kev_soft_from_qwen.jsonl"
        try:
            wait_http(f"http://127.0.0.1:{QWEN_UPSTREAM}/health", timeout=900)
            br_proc = start_proc(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "thinkfast.bridge",
                    "--upstream",
                    f"http://127.0.0.1:{QWEN_UPSTREAM}",
                    "--model",
                    str(QWEN_OUT / "merged"),
                    "--port",
                    str(QWEN_BRIDGE),
                ],
                cwd=REPO,
                log_path=DATA / "qwen_teacher_bridge.log",
            )
            wait_http(f"http://127.0.0.1:{QWEN_BRIDGE}/health", timeout=120)
            if not soft_path.exists():
                # build soft-label Kev JSONL for a few hundred / all train — use subset for short distill
                from thinkfast.suite import read_jsonl  # type: ignore

                # reuse distill_letters teacher calls via a small inline loop
                sys.path.insert(0, str(REPO))
                from cmdgate.distill_letters import teacher_p_true  # noqa: WPS433
                from cmdgate.questions import RISKY_ID, RISKY_QUESTION

                kev_rows = []
                with (DATA / "cmdrisk_kev_train.jsonl").open(encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i >= 2000:  # short distill budget
                            break
                        row = json.loads(line)
                        cmd = row["state"]["command"]
                        p = teacher_p_true(teacher_endpoint, cmd)
                        q = {**RISKY_QUESTION, "label": bool(row["questions"][RISKY_ID]["label"]),
                             "target": {"true": p, "false": 1.0 - p}}
                        kev_rows.append({"state": row["state"], "questions": {RISKY_ID: q}})
                        if (i + 1) % 50 == 0:
                            log(f"soft labels {i + 1}")
                with soft_path.open("w", encoding="utf-8") as f:
                    for row in kev_rows:
                        f.write(json.dumps(row) + "\n")
                log(f"wrote {soft_path} ({len(kev_rows)} rows)")
        finally:
            stop_proc(br_proc)
            stop_proc(up_proc)
            time.sleep(5)

        kd_kev = KEV / "runs" / "cmdrisk-0.8b-kd"
        if not ((kd_kev / "adapter_config.json").exists() or (kd_kev / "head.pt").exists()):
            run(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "kev.train",
                    "--data",
                    str(soft_path),
                    "--base",
                    "Qwen/Qwen3.5-0.8B-Base",
                    "--init_from",
                    str(KEV_OUT),
                    "--lr",
                    "2e-5",
                    "--epochs",
                    "1",
                    "--batch",
                    "1",
                    "--accum",
                    "8",
                    "--device",
                    "mps",
                    "--out",
                    str(kd_kev),
                ],
                cwd=KEV,
            )

        kev_proc = start_proc(
            [
                "uv",
                "run",
                "--extra",
                "serve",
                "python",
                "-m",
                "kev.serve",
                "--run",
                str(kd_kev),
                "--port",
                str(KEV_PORT),
                "--device",
                "mps",
            ],
            cwd=KEV,
            log_path=DATA / "kev_kd_serve.log",
        )
        try:
            wait_http(f"http://127.0.0.1:{KEV_PORT}/v1/models", timeout=900)
            kd_report = score(
                f"http://127.0.0.1:{KEV_PORT}/v1/systemone",
                kd_dir,
                "distill",
            )
        finally:
            stop_proc(kev_proc)

        if automation(kd_report) <= kev_auto:
            log(
                f"distill did not improve automation ({automation(kd_report):.4f} <= {kev_auto:.4f}); "
                "keeping pre-distill Kev"
            )
            kd_report = None
        else:
            log("distill improved validation automation; keeping kd Kev weights")

    patch_report_md(kev_report, qwen_report, kd_report)
    log("=== chain_after_kev done ===")
    (DATA / "chain_done.json").write_text(
        json.dumps(
            {
                "kev_auto": kev_auto,
                "qwen_auto": qwen_auto,
                "distill_kept": kd_report is not None,
                "teacher": teacher,
                "student": student,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"FATAL: {type(exc).__name__}: {exc}")
        raise
