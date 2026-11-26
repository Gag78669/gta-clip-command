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
            # Feature the facecam while preserving the complete gameplay image below it.
            cam_h = int(self.h * 0.35)  # 672
            game_h = self.h - cam_h    # 1248

            # Use a broad corner region so small facecams are less likely to be cut off.
            if webcam_position == "top_left":
                cam_crop = f"crop=iw/3:ih/3:0:0,scale={self.w}:{cam_h}"
            elif webcam_position == "bottom_right":
                cam_crop = f"crop=iw/3:ih/3:iw*2/3:ih*2/3,scale={self.w}:{cam_h}"
            elif webcam_position == "bottom_left":
                cam_crop = f"crop=iw/3:ih/3:0:ih*2/3,scale={self.w}:{cam_h}"
            else:  # top_right (most common)
                cam_crop = f"crop=iw/3:ih/3:iw*2/3:0,scale={self.w}:{cam_h}"

            filters.append(f"[0:v]split=2[in_cam][in_game]")
            filters.append(f"[in_cam]{cam_crop}[cam_out]")
            filters.append(f"[in_game]split=2[game_bg_src][game_fg_src]")
            filters.append(f"[game_bg_src]scale={self.w}:{game_h}:force_original_aspect_ratio=increase,crop={self.w}:{game_h},boxblur=25:5[game_bg]")
            game_frame_h = int(self.w * 9 / 16)
            filters.append(f"[game_fg_src]scale={self.w}:{game_frame_h}:force_original_aspect_ratio=decrease,pad={self.w}:{game_frame_h}:(ow-iw)/2:(oh-ih)/2[game_fg]")
            filters.append(f"[game_bg][game_fg]overlay=(W-w)/2:(H-h)/2[game_out]")
            filters.append(f"[cam_out][game_out]vstack=inputs=2[stacked]")
            base_label = "[stacked]"

        elif layout_mode == "podcast_split":
            # Isolate the left and right speakers, then stack them without losing either person.
            panel_h = self.h // 2
            filters.append(f"[0:v]split=2[left_src][right_src]")
            filters.append(f"[left_src]crop=iw/2:ih:0:0,split=2[left_bg_src][left_fg_src]")
            filters.append(f"[right_src]crop=iw/2:ih:iw/2:0,split=2[right_bg_src][right_fg_src]")
            filters.append(f"[left_bg_src]scale={self.w}:{panel_h}:force_original_aspect_ratio=increase,crop={self.w}:{panel_h},boxblur=25:5[left_bg]")
            filters.append(f"[left_fg_src]scale={self.w}:{panel_h}:force_original_aspect_ratio=decrease[left_fg]")
            filters.append(f"[left_bg][left_fg]overlay=(W-w)/2:(H-h)/2[left_panel]")
            filters.append(f"[right_bg_src]scale={self.w}:{panel_h}:force_original_aspect_ratio=increase,crop={self.w}:{panel_h},boxblur=25:5[right_bg]")
            filters.append(f"[right_fg_src]scale={self.w}:{panel_h}:force_original_aspect_ratio=decrease[right_fg]")
            filters.append(f"[right_bg][right_fg]overlay=(W-w)/2:(H-h)/2[right_panel]")
            filters.append(f"[left_panel][right_panel]vstack=inputs=2[podcast]")
            base_label = "[podcast]"

        elif layout_mode == "fit_blur":
            # Preserve the entire landscape frame; use a blurred copy to fill portrait space.
            frame_h = int(self.w * 9 / 16)
            filters.append(f"[0:v]split=2[fit_bg_src][fit_fg_src]")
            filters.append(f"[fit_bg_src]scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h},boxblur=30:8[fit_bg]")
            filters.append(f"[fit_fg_src]scale={self.w}:{frame_h}:force_original_aspect_ratio=decrease,pad={self.w}:{frame_h}:(ow-iw)/2:(oh-ih)/2[fit_fg]")
            filters.append(f"[fit_bg][fit_fg]overlay=(W-w)/2:(H-h)/2[fit_blur]")
            base_label = "[fit_blur]"

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
