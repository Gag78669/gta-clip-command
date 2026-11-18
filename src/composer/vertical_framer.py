"""FFmpeg filtergraph generator for 9:16 vertical short-form layouts."""

from src.core.logger import get_logger

logger = get_logger("composer.vertical_framer")


class VerticalFramer:
    """Constructs optimized FFmpeg video filtergraphs for vertical video framing."""

    def __init__(self, target_width: int = 1080, target_height: int = 1920):
        self.w = target_width
        self.h = target_height

    def get_filtergraph(
        self,
        layout_mode: str = "stacked",
        webcam_position: str = "top_right",
        subtitle_ass_path: str | None = None,
    ) -> str:
        """Build the FFmpeg -filter_complex string for the specified layout and subtitles."""
        filters = []

        if layout_mode == "stacked":
            # Stacked Layout: Top 35% (1080x672) facecam, Bottom 65% (1080x1248) gameplay
            cam_h = int(self.h * 0.35)  # 672
            game_h = self.h - cam_h    # 1248

            # Default facecam crop coordinates (assuming standard 1920x1080 source)
            if webcam_position == "top_left":
                cam_crop = f"crop=480:360:0:0,scale={self.w}:{cam_h}"
            elif webcam_position == "bottom_right":
                cam_crop = f"crop=480:360:1440:720,scale={self.w}:{cam_h}"
            elif webcam_position == "bottom_left":
                cam_crop = f"crop=480:360:0:720,scale={self.w}:{cam_h}"
            else:  # top_right (most common)
                cam_crop = f"crop=480:360:1440:0,scale={self.w}:{cam_h}"

            # Gameplay crop: Center 9:16 crop from the 16:9 gameplay frame
            game_crop = f"crop=935:1080:(in_w-935)/2:0,scale={self.w}:{game_h}"

            filters.append(f"[0:v]split=2[in_cam][in_game]")
            filters.append(f"[in_cam]{cam_crop}[cam_out]")
            filters.append(f"[in_game]{game_crop}[game_out]")
            filters.append(f"[cam_out][game_out]vstack=inputs=2[stacked]")
            base_label = "[stacked]"

        elif layout_mode == "blurred_pillar":
            # Blurred Background with Centered 16:9 Gameplay
            fg_h = int(self.w * 9 / 16)  # ~608
            filters.append(f"[0:v]split=2[in_bg][in_fg]")
            filters.append(f"[in_bg]scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h},boxblur=25:5[bg]")
            filters.append(f"[in_fg]scale={self.w}:{fg_h}[fg]")
            filters.append(f"[bg][fg]overlay=(W-w)/2:(H-h)/2[pillar]")
            base_label = "[pillar]"

        else:
            # Action Crop: Direct centered 9:16 crop
            filters.append(f"[0:v]crop=ih*9/16:ih:(iw-ow)/2:0,scale={self.w}:{self.h}[action_crop]")
            base_label = "[action_crop]"

        # Add Subtitle Burning Filter if subtitle path provided
        if subtitle_ass_path:
            # Escape Windows backslashes and colons for FFmpeg subtitles filter syntax
            escaped_path = (
                subtitle_ass_path.replace("\\", "/")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            filters.append(f"{base_label}subtitles='{escaped_path}'[v_out]")
            final_label = "[v_out]"
        else:
            final_label = base_label

        filtergraph = ";".join(filters)
        return filtergraph, final_label
