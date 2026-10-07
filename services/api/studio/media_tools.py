"""FFmpeg and ffprobe calls used by workflow V4.5: proxies and rough cuts for review, masters, stems and QC for delivery.

Review copies are H.264 at proxy size. Masters are ProRes 422 HQ at the series standard, tagged with the
standard's colour space; no LUT or grade is applied here, that is done in Resolve. Stems are 48 kHz 24-bit
stereo WAV of exactly the picture's length; nothing is normalised or limited, the mix is made in Resolve.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .domain import DomainError
from .tool_settings import ToolSettings


FRAME_FILE = re.compile(r"^(.*?)(\d+)\.(jpe?g|png)$", re.IGNORECASE)
# One pixel format and colour range for every clip, so segments from Unreal frames and from providers can be joined.
VIDEO_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv"]
TV_RANGE = "scale=out_range=tv,format=yuv420p,setparams=range=tv"
MASTER_CODEC = ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
MASTER_LABEL = "ProRes 422 HQ"
# Primaries, transfer and matrix written into the master for each series standard this app can conform to.
COLOR_TAGS = {"Rec.709": ("bt709", "bt709", "bt709"), "sRGB": ("bt709", "iec61966-2-1", "bt709")}
SOUND_RATE = 48000
STEM_CODEC = ["-c:a", "pcm_s24le", "-ar", str(SOUND_RATE), "-ac", "2"]
PREVIEW_CODEC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-color_range", "tv"]
# Clips per FFmpeg call when a stem is laid out; more are rendered in parts so the command line stays short.
MAX_STEM_INPUTS = 32


def master_color(color_space: str) -> tuple[str, list[str]]:
    """Filter that labels the frames, and the matching encoder arguments, so the tags reach the file."""
    if color_space not in COLOR_TAGS:
        raise DomainError(f"Chuẩn màu {color_space} của series cần pipeline quản lý màu riêng; app chỉ chuẩn hóa được Rec.709 và sRGB.", 409)
    primaries, transfer, matrix = COLOR_TAGS[color_space]
    return (f"scale=out_range=tv,format=yuv422p10le,setparams=range=tv:color_primaries={primaries}:color_trc={transfer}:colorspace={matrix}",
            ["-color_primaries", primaries, "-color_trc", transfer, "-colorspace", matrix, "-color_range", "tv", "-movflags", "+write_colr"])


class MediaTools:
    def __init__(self, studio, paths=None):
        self.studio = studio
        self._paths = paths

    def paths(self) -> dict:
        """The operator's FFmpeg from Settings; nothing is taken from PATH silently."""
        if self._paths:
            return self._paths
        tools = ToolSettings(self.studio).get()
        if not tools["configured"] or not tools["ffprobe_path"]:
            raise DomainError("Cấu hình FFmpeg (có ffprobe) ở Cài đặt → Công cụ xử lý video trước khi tạo proxy hoặc rough cut.", 409)
        return {"ffmpeg": tools["ffmpeg_path"], "ffprobe": tools["ffprobe_path"]}

    def available(self) -> bool:
        try:
            self.paths()
            return True
        except DomainError:
            return False

    def _run(self, tool: str, args: list[str], label: str, timeout: int = 3600, log: bool = False) -> str:
        """Standard output of the tool; with `log`, what it reported on standard error (where filters print their findings)."""
        try:
            done = subprocess.run([self.paths()[tool], *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DomainError(f"Không chạy được FFmpeg khi {label}: {type(exc).__name__}.", 409) from exc
        if done.returncode != 0:
            lines = [line for line in (done.stderr or "").strip().splitlines() if line.strip()]
            raise DomainError(f"FFmpeg lỗi khi {label}: {lines[-1][:240] if lines else 'không có thông báo'}", 409)
        return done.stderr if log else done.stdout

    def probe_video(self, path: Path) -> dict:
        """Counted frames, size and frame rate of the first video stream."""
        output = self._run("ffprobe", ["-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                                       "stream=nb_read_frames,width,height,r_frame_rate", "-of", "json", str(path)], "đọc thông tin video", 600)
        try:
            stream = json.loads(output)["streams"][0]
            num, den = (int(part) for part in stream["r_frame_rate"].split("/"))
            return {"frames": int(stream["nb_read_frames"]), "width": int(stream["width"]), "height": int(stream["height"]), "fps_num": num, "fps_den": den}
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
            raise DomainError("File đã chọn không có luồng video đọc được.", 409) from exc

    @staticmethod
    def _rate(fps: dict) -> str:
        return f"{fps['num']}/{fps['den']}"

    def probe_media(self, path: Path) -> dict:
        """Everything quality control needs about a clip: counted frames, size, rate, codec and whether it carries audio."""
        output = self._run("ffprobe", ["-v", "error", "-count_frames", "-show_entries",
                                       "stream=codec_type,codec_name,pix_fmt,nb_read_frames,width,height,r_frame_rate", "-of", "json", str(path)], "đọc thông tin video", 1800)
        try:
            streams = json.loads(output)["streams"]
            video = next(stream for stream in streams if stream["codec_type"] == "video")
            num, den = (int(part) for part in video["r_frame_rate"].split("/"))
            return {"frames": int(video["nb_read_frames"]), "width": int(video["width"]), "height": int(video["height"]), "fps_num": num, "fps_den": den,
                    "codec": video["codec_name"], "pix_fmt": video.get("pix_fmt"), "audio_streams": sum(stream["codec_type"] == "audio" for stream in streams)}
        except (KeyError, StopIteration, ValueError, ZeroDivisionError) as exc:
            raise DomainError("File đã chọn không có luồng video đọc được.", 409) from exc

    def _frame_input(self, folder: Path, fps: dict, frames: int) -> list[str]:
        """FFmpeg input arguments for an image sequence written by Unreal; the sequence has to be complete and contiguous."""
        found = sorted((int(m.group(2)), name) for name in (p.name for p in folder.iterdir() if p.is_file()) if (m := FRAME_FILE.match(name))) if folder.is_dir() else []
        if len(found) != frames or [number for number, _ in found] != list(range(found[0][0], found[0][0] + frames)):
            raise DomainError(f"Unreal ghi {len(found)} frame, shot cần {frames} frame liên tục; không dùng bộ frame thiếu.", 409)
        match = FRAME_FILE.match(found[0][1])
        pattern = f"{match.group(1).replace('%', '%%')}%0{len(match.group(2))}d.{match.group(3)}"
        return ["-framerate", self._rate(fps), "-start_number", str(found[0][0]), "-i", str(folder / pattern)]

    def encode_frames(self, folder: Path, target: Path, fps: dict, frames: int) -> None:
        """Image sequence written by Unreal → one proxy clip."""
        source = self._frame_input(folder, fps, frames)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("ffmpeg", ["-y", "-loglevel", "error", *source, "-vf", TV_RANGE, "-frames:v", str(frames), *VIDEO_CODEC, "-an", str(target)], "ghép frame thành proxy")

    def encode_master(self, folder: Path, target: Path, fps: dict, frames: int, color_space: str) -> None:
        """Final frames written by Unreal → one master clip, without any resize."""
        label, tags = master_color(color_space)
        source = self._frame_input(folder, fps, frames)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("ffmpeg", ["-y", "-loglevel", "error", *source, "-vf", label, "-frames:v", str(frames), *MASTER_CODEC, *tags, "-an", str(target)], "ghép frame final thành master")

    def conform_final(self, source: Path, target: Path, fps: dict, frames: int, width: int, height: int, color_space: str) -> None:
        """Step 11: one final at the series frame rate and size, cut at the shot length, without audio.
        Unlike a rough-cut segment nothing is held or padded in time; a source that is too short stays short and fails QC."""
        label, tags = master_color(color_space)
        chain = (f"fps={self._rate(fps)},scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                 f"trim=end_frame={frames},setpts=PTS-STARTPTS,{label}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("ffmpeg", ["-y", "-loglevel", "error", "-i", str(source), "-an", "-vf", chain, "-frames:v", str(frames), *MASTER_CODEC, *tags, str(target)], "chuẩn hóa final")

    def conform(self, source: Path, target: Path, fps: dict, frames: int, width: int, height: int) -> None:
        """One shot of the rough cut: exact frame count, common size and rate, no audio. A source that is too
        short holds its last frame; one that is longer is cut at the shot length."""
        chain = (f"fps={self._rate(fps)},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                 f"tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},setpts=PTS-STARTPTS,{TV_RANGE}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("ffmpeg", ["-y", "-loglevel", "error", "-i", str(source), "-an", "-vf", chain, "-frames:v", str(frames), *VIDEO_CODEC,
                             "-video_track_timescale", str(fps["num"] * 1000), str(target)], "chuẩn hóa shot cho rough cut")

    def join(self, segments: list[Path], audio: Path | None, target: Path, work: Path) -> None:
        """Segments end to end, plus the dialogue-only mix when there is one."""
        work.mkdir(parents=True, exist_ok=True)
        listing = work / "segments.txt"
        listing.write_text("".join("file '" + segment.as_posix().replace("'", "'\\''") + "'\n" for segment in segments), encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        inputs = ["-f", "concat", "-safe", "0", "-i", str(listing)] + (["-i", str(audio)] if audio else [])
        codecs = ["-c:v", "copy"] + (["-c:a", "aac", "-b:a", "160k", "-shortest"] if audio else ["-an"])
        self._run("ffmpeg", ["-y", "-loglevel", "error", *inputs, *codecs, "-movflags", "+faststart", str(target)], "ghép rough cut")

    def first_frame(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("ffmpeg", ["-y", "-loglevel", "error", "-i", str(source), "-frames:v", "1", str(target)], "lấy frame đầu của nguồn UE", 300)

    # ---- sound: stems, mix and loudness ----------------------------------------------------------------------------

    def probe_audio(self, path: Path) -> dict:
        """The first audio stream of a file the operator brings in, or of a finished preview."""
        output = self._run("ffprobe", ["-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels,duration:format=duration",
                                       "-of", "json", str(path)], "đọc thông tin âm thanh", 600)
        try:
            data = json.loads(output)
            stream = data["streams"][0]
            duration = stream.get("duration") if stream.get("duration") not in (None, "N/A") else data["format"]["duration"]
            return {"codec": stream["codec_name"], "sample_rate": int(stream["sample_rate"]), "channels": int(stream["channels"]), "duration_ms": round(float(duration) * 1000)}
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise DomainError("File đã chọn không có luồng âm thanh đọc được.", 409) from exc

    def stem_info(self, path: Path) -> dict:
        """Exact sample count of a WAV stem."""
        output = self._run("ffprobe", ["-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels,duration_ts,time_base", "-of", "json", str(path)],
                           "đọc thông tin stem", 600)
        try:
            stream = json.loads(output)["streams"][0]
            num, den = (int(part) for part in stream["time_base"].split("/"))
            rate = int(stream["sample_rate"])
            return {"sample_rate": rate, "channels": int(stream["channels"]), "samples": round(int(stream["duration_ts"]) * num * rate / den)}
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
            raise DomainError("Stem không đọc được như file WAV.", 409) from exc

    def extract_audio(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("ffmpeg", ["-y", "-loglevel", "error", "-i", str(source), "-vn", "-c:a", "pcm_s16le", str(target)], "tách âm thanh khỏi video")

    def render_stem(self, clips: list[dict], target: Path, samples: int) -> None:
        """One track of exactly `samples` samples. Each clip: path, start_sample and, optionally, length_samples,
        fade_in_samples, fade_out_samples (needs length_samples) and gain_db."""
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clips:
            self._run("ffmpeg", ["-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r={SOUND_RATE}:cl=stereo", "-af", f"atrim=end_sample={samples}",
                                 *STEM_CODEC, str(target)], "ghi stem trống")
            return
        if len(clips) > MAX_STEM_INPUTS:
            folder = target.with_name(target.stem + "-parts")
            try:
                parts = []
                for index in range(0, len(clips), MAX_STEM_INPUTS):
                    self.render_stem(clips[index:index + MAX_STEM_INPUTS], folder / f"part-{index}.wav", samples)
                    parts.append({"path": folder / f"part-{index}.wav", "start_sample": 0})
                self.render_stem(parts, target, samples)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
            return
        inputs, chains = [], []
        for index, clip in enumerate(clips):
            inputs += ["-i", str(clip["path"])]
            steps = [f"aresample={SOUND_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
            length = clip.get("length_samples")
            if length:
                steps.append(f"atrim=end_sample={length}")
            if clip.get("fade_in_samples"):
                steps.append(f"afade=t=in:start_sample=0:nb_samples={clip['fade_in_samples']}")
            if clip.get("fade_out_samples") and length:
                steps.append(f"afade=t=out:start_sample={max(0, length - clip['fade_out_samples'])}:nb_samples={clip['fade_out_samples']}")
            if clip.get("gain_db"):
                steps.append(f"volume={clip['gain_db']:.2f}dB")
            steps.append(f"adelay=delays={clip['start_sample']}S:all=1")
            chains.append(f"[{index}:a:0]{','.join(steps)}[a{index}]")
        tail = f"apad=whole_len={samples},atrim=end_sample={samples}[out]"
        chains.append("".join(f"[a{index}]" for index in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0:duration=longest,{tail}" if len(clips) > 1 else f"[a0]{tail}")
        self._run("ffmpeg", ["-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains), "-map", "[out]", *STEM_CODEC, str(target)], "ghép stem âm thanh")

    def loudness(self, path: Path) -> dict:
        """Integrated loudness, true peak and loudness range (EBU R128) as FFmpeg's loudnorm filter measures them."""
        log = self._run("ffmpeg", ["-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"], "đo loudness", log=True)
        try:
            data = json.loads(log[log.rindex("{"):log.rindex("}") + 1])
            number = lambda key: None if "inf" in str(data[key]) else float(data[key])
            return {"integrated_lufs": number("input_i"), "true_peak_dbtp": number("input_tp"), "range_lu": number("input_lra")}
        except (ValueError, KeyError) as exc:
            raise DomainError("Không đọc được kết quả đo loudness của FFmpeg.", 409) from exc

    # ---- delivery: preview and picture QC --------------------------------------------------------------------------

    def export_preview(self, masters: list[Path], audio: Path | None, target: Path, work: Path) -> None:
        """The masters end to end as one H.264 file at full size, with the mix when there is one."""
        work.mkdir(parents=True, exist_ok=True)
        listing = work / "masters.txt"
        listing.write_text("".join("file '" + master.as_posix().replace("'", "'\\''") + "'\n" for master in masters), encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        inputs = ["-f", "concat", "-safe", "0", "-i", str(listing)] + (["-i", str(audio)] if audio else [])
        sound = ["-c:a", "aac", "-b:a", "192k", "-shortest"] if audio else ["-an"]
        self._run("ffmpeg", ["-y", "-loglevel", "error", *inputs, "-vf", TV_RANGE, *PREVIEW_CODEC, *sound, "-movflags", "+faststart", str(target)], "xuất MP4 preview", 4 * 3600)

    def detect(self, path: Path) -> dict:
        """Black and frozen stretches of the picture, in seconds from the start."""
        log = self._run("ffmpeg", ["-hide_banner", "-nostats", "-i", str(path), "-an", "-vf", "blackdetect=d=0.1:pic_th=0.98:pix_th=0.10,freezedetect=n=0.001:d=1",
                                   "-f", "null", "-"], "kiểm tra frame đen và đứng hình", 4 * 3600, log=True)
        black = [{"start": float(start), "end": float(end)} for start, end in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
        starts, ends = re.findall(r"freeze_start: ([\d.]+)", log), re.findall(r"freeze_end: ([\d.]+)", log)
        return {"black": black, "freeze": [{"start": float(start), "end": float(ends[index]) if index < len(ends) else None} for index, start in enumerate(starts)]}
