"""Pure writers for the hand-off of workflow V4.5 step 13: FCPXML timeline, CMX3600 EDL and SRT subtitles.

The timeline starts at 00:00:00:00. The FCPXML carries one video clip per shot and every stem on its own
audio lane; the EDL is a picture-only fallback. Neither has been imported into DaVinci Resolve from this
environment; the operator confirms the import at C10.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

FCPXML_VERSION = "1.9"
ROLES = {"DX": "dialogue", "FOLEY": "effects.Foley", "SFX": "effects.SFX", "AMB": "effects.Ambience", "MUSIC": "music"}


def clock(frames: int, fps: dict) -> str:
    """Frames as an FCPXML time value on the frame grid of the sequence."""
    return "0s" if frames == 0 else f"{frames * fps['den']}/{fps['num']}s"


def milliseconds(frames: int, fps: dict) -> int:
    return (frames * 1000 * fps["den"] + fps["num"] // 2) // fps["num"]


def timecode(frames: int, fps: dict) -> str:
    """Non-drop timecode at the nominal frame rate."""
    base = round(fps["num"] / fps["den"])
    return f"{frames // (base * 3600):02d}:{frames // (base * 60) % 60:02d}:{frames // base % 60:02d}:{frames % base:02d}"


def format_name(width: int, height: int, fps: dict) -> str:
    rate = {(24, 1): "24", (25, 1): "25", (30, 1): "30", (50, 1): "50", (60, 1): "60", (24000, 1001): "2398", (30000, 1001): "2997", (60000, 1001): "5994"}.get((fps["num"], fps["den"]))
    return f"FFVideoFormat{height}p{rate}" if rate and (width, height) in ((1280, 720), (1920, 1080), (3840, 2160)) else "FFVideoFormatRateUndefined"


def fcpxml(name: str, fps: dict, width: int, height: int, shots: list[dict], stems: list[dict]) -> str:
    """`shots`: shot_id, path, frames, in timeline order. `stems`: role, path; each spans the whole timeline."""
    total = sum(shot["frames"] for shot in shots)
    attr = quoteattr
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE fcpxml>", f'<fcpxml version="{FCPXML_VERSION}">', "  <resources>",
             f'    <format id="r1" name={attr(format_name(width, height, fps))} frameDuration="{fps["den"]}/{fps["num"]}s" width="{width}" height="{height}"/>']
    for index, shot in enumerate(shots, 1):
        lines += [f'    <asset id="v{index}" name={attr(shot["shot_id"])} start="0s" duration="{clock(shot["frames"], fps)}" hasVideo="1" videoSources="1" format="r1" hasAudio="0">',
                  f'      <media-rep kind="original-media" src={attr(Path(shot["path"]).resolve().as_uri())}/>', "    </asset>"]
    for index, stem in enumerate(stems, 1):
        lines += [f'    <asset id="a{index}" name={attr(Path(stem["path"]).stem)} start="0s" duration="{clock(total, fps)}" hasVideo="0" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">',
                  f'      <media-rep kind="original-media" src={attr(Path(stem["path"]).resolve().as_uri())}/>', "    </asset>"]
    lines += ["  </resources>", "  <library>", f"    <event name={attr(name)}>", f"      <project name={attr(name)}>",
              f'        <sequence format="r1" duration="{clock(total, fps)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">', "          <spine>"]
    cursor = 0
    for index, shot in enumerate(shots, 1):
        clip = f'            <asset-clip ref="v{index}" name={attr(shot["shot_id"])} offset="{clock(cursor, fps)}" start="0s" duration="{clock(shot["frames"], fps)}" tcFormat="NDF"'
        if index == 1 and stems:
            # The stems hang from the first clip and run the whole timeline, one lane (audio track) per stem.
            lines.append(clip + ">")
            lines += [f'              <asset-clip ref="a{lane}" lane="-{lane}" name={attr(Path(stem["path"]).stem)} offset="0s" start="0s" duration="{clock(total, fps)}" audioRole={attr(ROLES[stem["role"]])}/>'
                      for lane, stem in enumerate(stems, 1)]
            lines.append("            </asset-clip>")
        else:
            lines.append(clip + "/>")
        cursor += shot["frames"]
    lines += ["          </spine>", "        </sequence>", "      </project>", "    </event>", "  </library>", "</fcpxml>", ""]
    return "\n".join(lines)


def edl(name: str, fps: dict, shots: list[dict]) -> str:
    lines = [f"TITLE: {name}", "FCM: NON-DROP FRAME", ""]
    cursor = 0
    for index, shot in enumerate(shots, 1):
        lines += [f"{index:03d}  AX       V     C        {timecode(0, fps)} {timecode(shot['frames'], fps)} {timecode(cursor, fps)} {timecode(cursor + shot['frames'], fps)}",
                  f"* FROM CLIP NAME: {Path(shot['path']).name}", ""]
        cursor += shot["frames"]
    return "\n".join(lines)


def srt(cues: list[dict], fps: dict) -> str:
    """`cues`: start_frame, end_frame, text. Cues without text are left out and the rest renumbered."""
    def stamp(frames: int) -> str:
        value = milliseconds(frames, fps)
        return f"{value // 3600000:02d}:{value // 60000 % 60:02d}:{value // 1000 % 60:02d},{value % 1000:03d}"
    blocks = [f"{number}\n{stamp(cue['start_frame'])} --> {stamp(cue['end_frame'])}\n{cue['text'].strip()}\n"
              for number, cue in enumerate((cue for cue in cues if cue["text"].strip()), 1)]
    return "\n".join(blocks)
