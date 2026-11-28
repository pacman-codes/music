"""Offline audio processing: immutable derivatives and measured acoustic features.

The HTTP server never runs FFmpeg. A separate, opt-in single worker prepares
local original assets. No URLs, shell commands or client-provided paths.
"""
from __future__ import annotations

import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = 1
RATE = 8000
SAMPLE_SECONDS = 90
MAX_DURATION = 7200
MAX_DERIVED = 100 * 1024**2
DERIVED_QUOTA = 2 * 1024**3
DISK_RESERVE = 1024**3
MAX_ATTEMPTS = 3
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _run(args, timeout):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=timeout).stdout


def probe(path):
    raw = _run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mp3,mov,mp4,m4a,flac,ogg",
                "-select_streams", "a:0", "-show_entries",
                "stream=codec_name,sample_rate,channels:format=duration,bit_rate",
                "-of", "json", str(path)], 15)
    data = json.loads(raw)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError("Unsupported duration")
    bitrate=data["format"].get("bit_rate")
    return {"duration_ms": round(duration * 1000),
            "codec": str(stream["codec_name"])[:32],
            "sample_rate": int(stream["sample_rate"]),
            "channels": int(stream["channels"]),
            "bitrate": int(bitrate) if str(bitrate).isdigit() else 0}


def sample_features(raw):
    """Approximate low/mid/high energy and dynamics; no learned genre/mood claims."""
    pcm = array.array("h")
    pcm.frombytes(raw[:len(raw)//2*2])
    if sys.byteorder != "little":
        pcm.byteswap()
    if len(pcm) < RATE:
        raise ValueError("Insufficient decoded audio")
    if len(pcm) > RATE * SAMPLE_SECONDS:
        raise ValueError("Sample exceeds bound")
    low = mid = 0.0
    energies = [0.0, 0.0, 0.0]
    frames = []
    energy = peak = frame = 0.0
    crossings = 0
    previous = 0.0
    frame_len = RATE // 50
    # Fixed one-pole filters; approximate frequency balance, not a spectrum.
    a = 1 - math.exp(-2 * math.pi * 180 / RATE)
    b = 1 - math.exp(-2 * math.pi * 1600 / RATE)
    for i, value in enumerate(pcm):
        x = value / 32768
        low += a * (x-low)
        mid += b * (x-mid)
        bands = (low, mid-low, x-mid)
        for j, v in enumerate(bands):
            energies[j] += v*v
        e = x*x
        energy += e
        frame += e
        peak = max(peak, abs(x))
        crossings += int((x > 0) != (previous > 0))
        previous = x
        if (i+1) % frame_len == 0:
            frames.append(math.sqrt(frame/frame_len))
            frame = 0.0
    rms = math.sqrt(energy/len(pcm))
    if rms < 0.0001:
        raise ValueError("Silence or unusable sample")
    total = sum(energies)
    ordered = sorted(frames)
    quantile = lambda p: ordered[min(len(ordered)-1, int(p*len(ordered)))]
    dbfs = lambda v: 20*math.log10(max(v, 1e-6))
    # Positive energy changes and normalized autocorrelation at musical periods.
    onset = [max(0.0, frames[i]-frames[i-1]) for i in range(1,len(frames))]
    norm = sum(x*x for x in onset)
    tempo = None
    confidence = 0.0
    if norm > 1e-6 and len(onset) >= 500:
        correlations = [(sum(x*y for x,y in zip(onset[lag:], onset[:-lag]))/norm,lag)
                        for lag in range(17,51)]
        score,lag = max(correlations)
        # Ambiguous half/double-tempo estimates are deliberately not asserted
        # as exact BPM. A weak peak produces no estimate.
        if score >= .30:
            tempo = round(3000/lag,1)
            confidence = round(min(1.0,score),3)
    return {"sample_seconds":round(len(pcm)/RATE,2),
            "rms_dbfs":round(dbfs(rms),2),
            "crest_db":round(dbfs(peak)-dbfs(rms),2),
            "dynamic_db":round(min(60,dbfs(quantile(.9))-dbfs(quantile(.1))),2),
            "low":round(energies[0]/total,5), "mid":round(energies[1]/total,5),
            "high":round(energies[2]/total,5),
            "zero_crossing":round(crossings/len(pcm),5),
            "silence_fraction":round(sum(v<.003 for v in frames)/len(frames),5),
            "tempo_estimate":tempo,"tempo_confidence":confidence}


def analyze(path):
    result = probe(path)
    # Analyze a bounded excerpt near the middle, not only an intro.
    start = max(0, (result["duration_ms"]/1000-SAMPLE_SECONDS)/2)
    raw = _run(["ffmpeg","-v","error","-nostdin","-protocol_whitelist","file,pipe","-format_whitelist","mp3,mov,mp4,m4a,flac,ogg",
                "-threads","1","-ss",str(start),"-i",str(path),"-map","0:a:0",
                "-t",str(SAMPLE_SECONDS),"-vn","-sn","-dn","-ac","1","-ar",str(RATE),
                "-f","s16le","pipe:1"], 75)
    return {**result, **sample_features(raw)}


def encode_economy(path, target):
    info=probe(path)
    subprocess.run(["ffmpeg","-v","error","-nostdin","-protocol_whitelist","file,pipe","-format_whitelist","mp3,mov,mp4,m4a,flac,ogg",
                    "-threads","1","-i",str(path),"-map","0:a:0","-vn","-sn","-dn",
                    "-map_metadata","-1","-c:a","aac","-b:a","96k","-ac","2",
                    "-ar","44100","-threads","1","-movflags","+faststart",
                    "-fs",str(MAX_DERIVED),"-f","mp4","-y",str(target)],
                   check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=240)
    out=probe(target)
    size=target.stat().st_size
    if not 0<size<MAX_DERIVED or abs(out["duration_ms"]-info["duration_ms"])>max(1000,info["duration_ms"]*.01):
        raise ValueError("Incomplete derivative")
    return size


def distance(a,b):
    # This is descriptor distance, NOT an AI embedding or a calibrated probability.
    terms=[(a[k]-b[k])**2 for k in ("low","mid","high","zero_crossing","silence_fraction")]
    terms += [((a["crest_db"]-b["crest_db"])/30)**2,
              ((a["dynamic_db"]-b["dynamic_db"])/60)**2]
    if a.get("tempo_estimate") and b.get("tempo_estimate"):
        x,y=a["tempo_estimate"],b["tempo_estimate"]
        ratio=min(abs(math.log2(x/y)-n) for n in (-1,0,1))
        terms.append(.35*ratio*ratio)
    return math.sqrt(sum(terms)/len(terms))


class MediaProcessor:
    def __init__(self,store):
        self.store,self.db=store,store.library
        self.root=store.root.parent/"derived"
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with self.db.con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS native_media_processing(
                sha256 TEXT NOT NULL, version INTEGER NOT NULL,
                requested_at INTEGER NOT NULL DEFAULT 0,
                features TEXT, analysis_attempts INTEGER NOT NULL DEFAULT 0,
                analysis_due INTEGER NOT NULL DEFAULT 0,
                economy_size INTEGER, economy_attempts INTEGER NOT NULL DEFAULT 0,
                economy_due INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(sha256,version));
            """)

    def _sha(self,tid):
        with self.db.con() as con:
            row=con.execute("SELECT sha256 FROM native_audio WHERE track_id=?",(tid,)).fetchone()
        return row[0] if row and SHA.fullmatch(row[0]) else None

    def _target(self,sha):
        if not SHA.fullmatch(sha):raise ValueError("Invalid asset hash")
        return self.root/f"{sha}-v{VERSION}.m4a"

    def request(self,ids):
        now=int(time.time())
        with self.db.con() as con:
            for tid in dict.fromkeys(ids):
                row=con.execute("SELECT sha256 FROM native_audio WHERE track_id=?",(tid,)).fetchone()
                if row and SHA.fullmatch(row[0]):
                    con.execute("""INSERT INTO native_media_processing(sha256,version,requested_at)
                       VALUES(?,?,?) ON CONFLICT(sha256,version) DO UPDATE SET requested_at=excluded.requested_at""",
                                (row[0],VERSION,now))
                    # If a previously generated derivative disappeared, permit regeneration.
                    result=con.execute("SELECT economy_size FROM native_media_processing WHERE sha256=? AND version=?",(row[0],VERSION)).fetchone()
                    if result and result[0] is not None and result[0]>0:
                        target=self._target(row[0])
                        try:valid=not target.is_symlink() and target.is_file() and target.stat().st_size==result[0]
                        except OSError:valid=False
                        if not valid:
                            con.execute("UPDATE native_media_processing SET economy_size=NULL,economy_attempts=0,economy_due=0 WHERE sha256=? AND version=?",(row[0],VERSION))

    def asset(self,tid):
        original=self.store.audio(tid)
        sha=self._sha(tid)
        if not original or not sha:return None
        with self.db.con() as con:
            row=con.execute("SELECT economy_size FROM native_media_processing WHERE sha256=? AND version=?",(sha,VERSION)).fetchone()
        if not row or row[0] is None:return None
        if row[0]==0:return (*original,"original")
        path=self._target(sha)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=row[0]:return None
        except OSError:return None
        return (path,"audio/mp4",row[0],"economy")

    def info(self,tid):
        sha=self._sha(tid)
        if not sha or not self.store.audio(tid):return {"analysis":None,"analysis_state":"unavailable","economy_state":"unavailable"}
        with self.db.con() as con:
            row=con.execute("SELECT * FROM native_media_processing WHERE sha256=? AND version=?",(sha,VERSION)).fetchone()
        asset=self.asset(tid)
        features=json.loads(row["features"]) if row and row["features"] else None
        return {"analysis":features,"analysis_state":"ready" if features else "failed" if row and row["analysis_attempts"]>=MAX_ATTEMPTS else "pending",
                "economy_state":"ready" if asset else "failed" if row and row["economy_attempts"]>=MAX_ATTEMPTS else "pending",
                "economy_size":asset[2] if asset else None,
                "economy_representation":asset[3] if asset else None,
                "version":VERSION}

    def analyzed_tracks(self):
        with self.db.con() as con:
            rows=con.execute("""SELECT a.track_id,a.sha256,p.features FROM native_audio a
                 JOIN native_media_processing p ON p.sha256=a.sha256 AND p.version=?
                 WHERE p.features IS NOT NULL ORDER BY a.track_id DESC LIMIT 10000""",(VERSION,)).fetchall()
        if not rows:return {}
        available=self.store.available_ids()
        return {r["track_id"]:(r["sha256"],json.loads(r["features"])) for r in rows if r["track_id"] in available}

    def similar(self,tid,limit=20):
        rows=self.analyzed_tracks()
        if tid not in rows:return []
        sha,seed=rows[tid]
        ratings=self.db.ratings()
        excluded=self.db.suppressed_verdict_track_ids()|self.db.disliked_ids()
        ranked=sorted((distance(seed,features),other) for other,(ident,features) in rows.items()
                      if ident!=sha and other not in excluded and ratings.get(other,10)>=5)
        seen=set()
        results=[]
        for score,other in ranked:
            ident=rows[other][0]
            if ident in seen:continue
            seen.add(ident)
            results.append((other,round(score,5)))
            if len(results)>=limit:break
        return results

    def affinity(self):
        rows=self.analyzed_tracks()
        liked=self.db.liked_ids()
        ratings=self.db.ratings()
        excluded=self.db.suppressed_verdict_track_ids()|self.db.disliked_ids()
        seeds=[(sha,f) for tid,(sha,f) in rows.items() if tid not in excluded
               and ratings.get(tid,10)>=5 and (tid in liked or ratings.get(tid,0)>=8)]
        seeds=list(dict(seeds).items())[:12]
        if not seeds:return {}
        return {tid:max(0.0,1-min(distance(f,seed) for ident,seed in seeds)/.35)
                for tid,(sha,f) in rows.items() if tid not in excluded and all(sha!=ident for ident,_ in seeds)}

    def run_once(self):
        """One local job, guarded by a filesystem lock released on worker crash."""
        import fcntl
        with (self.root/"worker.lock").open("a") as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return False
            now=int(time.time())
            for stale in self.root.glob("*.part"):
                stale.unlink(missing_ok=True)
            with self.db.con() as con:
                con.execute("""INSERT OR IGNORE INTO native_media_processing(sha256,version)
                         SELECT DISTINCT sha256,? FROM native_audio""",(VERSION,))
                rows=con.execute("""SELECT p.*,MIN(a.track_id) AS tid FROM native_media_processing p
                         JOIN native_audio a ON a.sha256=p.sha256 WHERE p.version=?
                         AND ((p.features IS NULL AND p.analysis_attempts<? AND p.analysis_due<=?)
                           OR (p.economy_size IS NULL AND p.economy_attempts<? AND p.economy_due<=?))
                         GROUP BY p.sha256,p.version
                         ORDER BY p.requested_at DESC,tid DESC""",
                         (VERSION,MAX_ATTEMPTS,now,MAX_ATTEMPTS,now)).fetchall()
            used=sum(p.stat().st_size for p in self.root.glob("*.m4a") if p.is_file() and not p.is_symlink())
            can_encode=(used+MAX_DERIVED<=DERIVED_QUOTA and shutil.disk_usage(self.root).free>=DISK_RESERVE+MAX_DERIVED)
            for row in rows:
                sha,tid=row["sha256"],row["tid"]
                original=self.store.audio(tid)
                if not SHA.fullmatch(sha) or not original:continue
                analysis_due=not row["features"] and row["analysis_attempts"]<MAX_ATTEMPTS and row["analysis_due"]<=now
                economy_due=(row["economy_size"] is None and can_encode and row["economy_attempts"]<MAX_ATTEMPTS
                             and row["economy_due"]<=now)
                if not analysis_due and not economy_due:continue
                # Prioritize bandwidth preparation for explicitly requested assets.
                stage="economy" if economy_due and (row["requested_at"] or not analysis_due) else "analysis"
                attempt=row[f"{stage}_attempts"]+1
                with self.db.con() as con:
                    con.execute(f"UPDATE native_media_processing SET {stage}_attempts=?,{stage}_due=? WHERE sha256=? AND version=?",
                                (attempt,now+60*2**(attempt-1),sha,VERSION))
                temp=self.root/f"{sha}-v{VERSION}.part"
                try:
                    if stage=="analysis":
                        result=json.dumps(analyze(original[0]),allow_nan=False)
                        with self.db.con() as con:
                            con.execute("UPDATE native_media_processing SET features=? WHERE sha256=? AND version=?",(result,sha,VERSION))
                    else:
                        temp.unlink(missing_ok=True)
                        size=encode_economy(original[0],temp)
                        # Keep a derivative only if it actually saves bytes.
                        if size<original[2]:
                            os.replace(temp,self._target(sha))
                        else:
                            size=0
                        with self.db.con() as con:
                            con.execute("UPDATE native_media_processing SET economy_size=? WHERE sha256=? AND version=?",(size,sha,VERSION))
                except (OSError,ValueError,KeyError,IndexError,subprocess.SubprocessError):
                    pass
                finally:temp.unlink(missing_ok=True)
                return True
            return False


def worker():
    from music_bot.native_storage import NativeStorage
    from music_bot.storage import Library
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("Install FFmpeg for the standalone media worker")
    root=Path(os.environ.get("LAIN_DATA_DIR","data/standalone"))
    media=MediaProcessor(NativeStorage(Library(root/"music.db"),root/"audio"))
    while True:
        worked=media.run_once()
        time.sleep(2 if worked else 15)


if __name__=="__main__":
    worker()
