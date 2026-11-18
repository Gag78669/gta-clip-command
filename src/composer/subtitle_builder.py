"""Generates animated ASS (Advanced SubStation Alpha) subtitles with word bounce and hook cards."""

from pathlib import Path
from src.signals.transcriber import WordTiming
from src.core.logger import get_logger

logger = get_logger("composer.subtitles")


class SubtitleBuilder:
    """Creates styled ASS subtitles formatted specifically for 9:16 vertical short-form video."""

    def __init__(self, target_width: int = 1080, target_height: int = 1920):
        self.target_width = target_width
        self.target_height = target_height

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Convert float seconds to ASS timestamp format: H:MM:SS.cs (centiseconds)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centis = int(round((seconds - int(seconds)) * 100))
        if centis >= 100:
            centis = 99
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

    def build_ass_script(
        self,
        words: list[WordTiming],
        clip_start_offset: float,
        hook_text: str | None = None,
        style_preset: str = "neon_yellow_bounce",
    ) -> str:
        """Construct the complete ASS script string with karaoke highlighting and viral hook."""
        # Define ASS header and styles
        ass_lines = [
            "[Script Info]",
            "Title: Gaming Clip AutoSub",
            "ScriptType: v4.00+",
            f"PlayResX: {self.target_width}",
            f"PlayResY: {self.target_height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # Viral Captions Style (Centered lower-middle)
            "Style: ViralCaption,Arial Black,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,6,3,2,60,60,650,1",
            # Hook Card Style (Upper-middle screen)
            "Style: HookCard,Arial Black,74,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,7,4,8,80,80,320,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        # 1. Add Viral Hook Card for the first 3.0 seconds
        if hook_text:
            hook_start = self._format_timestamp(0.0)
            hook_end = self._format_timestamp(3.2)
            clean_hook = hook_text.replace("\n", " ").strip()
            ass_lines.append(
                f"Dialogue: 1,{hook_start},{hook_end},HookCard,,0,0,0,,{{\\fad(150,250)}}{clean_hook}"
            )

        if not words:
            return "\n".join(ass_lines)

        # 2. Normalize word timings relative to clip start
        rel_words = []
        for w in words:
            r_start = max(0.0, w.start - clip_start_offset)
            r_end = max(0.0, w.end - clip_start_offset)
            if r_end > r_start:
                rel_words.append((w.word.strip().upper(), r_start, r_end))

        if not rel_words:
            return "\n".join(ass_lines)

        # 3. Group words into rapid short-burst phrases (2 to 4 words max)
        chunk_size = 3
        for i in range(0, len(rel_words), chunk_size):
            chunk = rel_words[i : i + chunk_size]
            chunk_start = chunk[0][1]
            chunk_end = chunk[-1][2]

            # Generate dialogue lines for each word in the chunk being actively spoken
            for active_idx, (active_word, w_start, w_end) in enumerate(chunk):
                start_ts = self._format_timestamp(w_start)
                end_ts = self._format_timestamp(w_end)

                # Format the line: active word highlighted in gold, others white
                line_parts = []
                for idx, (word_text, _, _) in enumerate(chunk):
                    if idx == active_idx:
                        # Highlight active word with scale pop and yellow color
                        line_parts.append(f"{{\\c&H00D7FF&\\fscx108\\fscy108}}{word_text}{{\\rViralCaption}}")
                    else:
                        line_parts.append(word_text)

                formatted_text = " ".join(line_parts)
                ass_lines.append(
                    f"Dialogue: 0,{start_ts},{end_ts},ViralCaption,,0,0,0,,{formatted_text}"
                )

        return "\n".join(ass_lines)

    def write_ass_file(
        self,
        output_path: Path | str,
        words: list[WordTiming],
        clip_start_offset: float,
        hook_text: str | None = None,
        style_preset: str = "neon_yellow_bounce",
    ) -> Path:
        """Generate and save the ASS subtitle file to disk."""
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        content = self.build_ass_script(
            words=words,
            clip_start_offset=clip_start_offset,
            hook_text=hook_text,
            style_preset=style_preset,
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("Generated ASS subtitle file: %s (%d bytes)", output_path.name, len(content))
        return output_path
