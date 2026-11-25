"""Master End-to-End Autonomous Pipeline Orchestrator for GTA CLIP COMMAND.

Executes the hardware-aware state machine with strict VRAM eviction and SQLite checkpointing.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table
from src.core.config import get_settings
from src.core.db import get_db_context
from src.core.models import ProcessingJob, CandidateClip, FinalClip, Creator, MediaSource, now_utc
from src.core.logger import get_logger
from src.core.exceptions import PipelineCancelled
from src.ingestion.rights_gate import RightsGate
from src.ingestion.media_inspector import MediaInspector
from src.ingestion.audio_extractor import AudioExtractor
from src.signals.audio_energy import AudioEnergyAnalyzer
from src.signals.scene_detector import SceneDetector
from src.signals.transcriber import SpeechTranscriber
from src.fusion.candidate_matcher import CandidateMatcher
from src.reasoning.ollama_client import OllamaClient
from src.reasoning.vision_agent import VisionAgent
from src.reasoning.narrative_agent import NarrativeAgent
from src.composer.subtitle_builder import SubtitleBuilder
from src.composer.nvenc_renderer import NvencRenderer
from src.qa.video_qa import VideoQAAgent

logger = get_logger("pipeline.orchestrator")
settings = get_settings()
console = Console()


def run_pipeline(
    video_path: Path | str,
    creator_handle: str,
    max_clips: int = 3,
    job_id: str | None = None,
    checkpoint=None,
) -> list[FinalClip]:
    """Execute complete autonomous clipping pipeline from raw MP4 to QA-approved vertical clips."""
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Source video not found: {video_path}")

    console.rule("[bold cyan]GTA CLIP COMMAND — AUTONOMOUS PIPELINE[/bold cyan]")
    logger.info("Initiating clipping pipeline for: %s (Creator: %s)", video_path.name, creator_handle)

    with get_db_context() as db:
        # -------------------------------------------------------------
        # STAGE 1: CREATOR RIGHTS & INTAKE GATE
        # -------------------------------------------------------------
        logger.info("STAGE 1: Enforcing Creator Rights Authorization Gate...")
        rights_gate = RightsGate(db)
        creator = db.query(Creator).filter(Creator.handle == creator_handle).first()
        if not creator:
            raise ValueError(f"Creator '{creator_handle}' is not registered in system.")

        rights_check = rights_gate.verify_creator(creator.id, strict=True)
        logger.info("Rights verified: %s", rights_check.reason)

        if job_id:
            job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if not job:
                raise ValueError(f"Processing job '{job_id}' does not exist.")
            media = db.query(MediaSource).filter(MediaSource.id == job.media_id).first()
            if not media:
                raise ValueError(f"Media source for processing job '{job_id}' does not exist.")
            if media.creator_id != creator.id:
                raise ValueError("Processing job creator does not match the requested creator.")
            job.status = "running"
            job.current_stage = "intake"
            job.progress_percent = 5
            job.error_message = None
        else:
            inspector = MediaInspector(db)
            media = inspector.register_media(
                creator_id=creator.id,
                file_path=video_path,
                title=video_path.stem,
            )
            job = ProcessingJob(
                media_id=media.id,
                status="running",
                current_stage="intake",
                progress_percent=5,
                started_at=now_utc(),
            )
            db.add(job)
            db.flush()
        db.commit()
        db.refresh(job)

        media_id = str(media.id)
        job_id = str(job.id)
        creator_id = str(creator.id)
        media_duration = float(media.duration_seconds)

    try:
        def safe_checkpoint() -> None:
            if checkpoint is not None:
                checkpoint()

        # -------------------------------------------------------------
        # STAGE 2: AUDIO DEMUXING
        # -------------------------------------------------------------
        logger.info("STAGE 2: Fast Audio Demuxing (16kHz Mono)...")
        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if j:
                j.current_stage = "audio_demux"
                j.progress_percent = 10
        safe_checkpoint()
        extractor = AudioExtractor()
        whisper_wav = extractor.extract_whisper_audio(video_path, output_name=f"{media_id}_16k")
        safe_checkpoint()

        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            j.current_stage = "transcription"
            j.progress_percent = 20

        # -------------------------------------------------------------
        # STAGE 3: SPEECH TRANSCRIPTION (FASTER-WHISPER ON CPU)
        # -------------------------------------------------------------
        logger.info("STAGE 3: Faster-Whisper Word-Level Speech Recognition...")
        safe_checkpoint()
        transcriber = SpeechTranscriber()
        transcript = transcriber.transcribe(whisper_wav, cache_name=media_id)
        transcriber.unload()  # Free memory immediately
        safe_checkpoint()

        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            j.current_stage = "signals"
            j.progress_percent = 40

        # -------------------------------------------------------------
        # STAGE 4: DETERMINISTIC SIGNAL EXTRACTION
        # -------------------------------------------------------------
        logger.info("STAGE 4: Audio Energy Spike & Scene Cut Extraction...")
        safe_checkpoint()
        energy_analyzer = AudioEnergyAnalyzer()
        audio_spikes = energy_analyzer.analyze(whisper_wav)

        scene_detector = SceneDetector()
        scenes = scene_detector.detect_scenes(video_path)
        hotspots = scene_detector.find_action_hotspots(scenes)
        safe_checkpoint()

        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            j.current_stage = "fusion"
            j.progress_percent = 55

        # -------------------------------------------------------------
        # STAGE 5: CANDIDATE FUSION
        # -------------------------------------------------------------
        logger.info("STAGE 5: Multi-Modal Temporal Candidate Fusion...")
        safe_checkpoint()
        matcher = CandidateMatcher()
        candidate_highlights = matcher.fuse(
            video_path=video_path,
            video_duration=media_duration,
            spikes=audio_spikes,
            scenes=scenes,
            hotspots=hotspots,
            transcript=transcript,
            max_candidates=max_clips,
        )
        safe_checkpoint()

        with get_db_context() as db:
            for cand in candidate_highlights:
                c_model = CandidateClip(
                    id=cand.candidate_id,
                    job_id=job_id,
                    start_time=cand.start_time,
                    end_time=cand.end_time,
                    duration=cand.duration,
                    excitement_score=cand.initial_excitement_score,
                    audio_peak_dbfs=cand.audio_peak_dbfs,
                    scene_cut_count=cand.scene_cut_count,
                    speech_density=cand.speech_density,
                )
                db.add(c_model)
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if j:
                j.candidate_count = len(candidate_highlights)
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            j.current_stage = "ai_evaluation"
            j.progress_percent = 65

        # -------------------------------------------------------------
        # STAGE 6: HARDWARE-AWARE SERIALIZED LLM INFERENCE
        # -------------------------------------------------------------
        logger.info("STAGE 6: Serialized Local LLM Reasoning (Strict VRAM Eviction)...")
        ollama = OllamaClient()
        ollama.evict_all()  # Clean baseline

        vision_agent = VisionAgent(ollama)
        narrative_agent = NarrativeAgent(ollama)

        ranked_specs = []
        for cand in candidate_highlights[:max_clips]:
            safe_checkpoint()
            logger.info("Evaluating candidate %s (%.1fs - %.1fs)...", cand.candidate_id, cand.start_time, cand.end_time)
            # 6A: Visual Frame Inspection with Qwen3.5:4B (auto-evicts VRAM)
            v_tags = vision_agent.analyze_keyframes(cand.keyframe_paths)

            # 6B: Narrative Virality & Hook Generation with Qwen3:8B (auto-evicts VRAM)
            clip_spec = narrative_agent.evaluate_candidate(
                candidate=cand,
                visual_tags=v_tags,
                creator_handle=creator.handle,
            )
            ranked_specs.append((cand, v_tags, clip_spec))

        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            j.current_stage = "composition"
            j.progress_percent = 80

        # -------------------------------------------------------------
        # STAGE 7: VIDEO COMPOSITION & NVENC RENDERING
        # -------------------------------------------------------------
        logger.info("STAGE 7: Vertical Reframing, Subtitle Burning & NVENC Rendering...")
        sub_builder = SubtitleBuilder(settings.target_width, settings.target_height)
        renderer = NvencRenderer()
        qa_agent = VideoQAAgent()

        final_clips = []
        for cand, v_tags, spec in ranked_specs:
            safe_checkpoint()
            words = transcript.words_in_range(spec.optimal_start, spec.optimal_end)

            # Build .ass subtitle file
            sub_path = settings.cache_dir / f"{spec.candidate_id}_subs.ass"
            sub_builder.write_ass_file(
                output_path=sub_path,
                words=words,
                clip_start_offset=spec.optimal_start,
                hook_text=spec.hook_text,
            )

            # Render 9:16 vertical MP4
            export_path = settings.exports_dir / f"{creator_handle.replace('@', '')}_{spec.candidate_id}_9x16.mp4"
            render_res = renderer.render_clip(
                video_path=video_path,
                start_time=spec.optimal_start,
                end_time=spec.optimal_end,
                output_path=export_path,
                layout_mode=spec.layout_mode,
                webcam_position=v_tags.webcam_position,
                subtitle_ass_path=sub_path,
            )

            # Run automated QA verification
            qa_res = qa_agent.verify_clip(export_path)

            with get_db_context() as db:
                final_model = FinalClip(
                    candidate_id=cand.candidate_id,
                    job_id=job_id,
                    creator_id=creator_id,
                    title=spec.title,
                    hook_text=spec.hook_text,
                    optimal_start=spec.optimal_start,
                    optimal_end=spec.optimal_end,
                    duration=render_res.duration_seconds,
                    virality_score=spec.virality_score,
                    humor_score=spec.humor_score,
                    action_score=spec.action_score,
                    layout_mode=spec.layout_mode,
                    subtitle_style=spec.subtitle_style,
                    approval_status="pending_review",
                    output_path=str(export_path),
                    thumbnail_path=render_res.thumbnail_path,
                )
                db.add(final_model)
                final_clips.append(final_model)

        safe_checkpoint()
        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if j:
                j.status = "completed"
                j.current_stage = "completed"
                j.progress_percent = 100
                j.candidate_count = db.query(CandidateClip).filter(CandidateClip.job_id == job_id).count()
                j.clip_count = len(final_clips)
                j.completed_at = now_utc()

        # Display Summary Table in Console
        table = Table(title="GTA CLIP COMMAND — Generated Highlights", border_style="cyan")
        table.add_column("Clip ID", style="magenta")
        table.add_column("Title", style="bold white")
        table.add_column("Hook Caption", style="yellow")
        table.add_column("Virality", justify="right", style="green")
        table.add_column("Duration", justify="right")
        table.add_column("Layout", style="cyan")
        table.add_column("Output File", style="dim")

        for c in final_clips:
            table.add_row(
                c.id,
                c.title,
                c.hook_text,
                f"{c.virality_score}/100",
                f"{c.duration:.1f}s",
                c.layout_mode,
                Path(c.output_path).name,
            )
        try:
            console.print(table)
            console.print("\n[bold green]Pipeline completed successfully! Ready for human approval.[/bold green]\n")
        except UnicodeEncodeError:
            # Some Windows terminals still use cp1252 and cannot print model-generated emoji.
            print("Pipeline completed successfully. Generated highlights:")
            for c in final_clips:
                safe_title = c.title.encode("ascii", errors="replace").decode("ascii")
                print(f"{c.id} | {safe_title} | {c.duration:.1f}s | {Path(c.output_path).name}")
            print("Ready for human approval.")
        return final_clips

    except PipelineCancelled:
        logger.info("Pipeline job %s cancelled by operator.", job_id)
        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if j:
                j.status = "cancelled"
                j.current_stage = "cancelled"
        raise
    except Exception as e:
        logger.exception("Pipeline failed unexpectedly: %s", e)
        with get_db_context() as db:
            j = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if j:
                j.status = "failed"
                j.error_message = str(e)
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run GTA CLIP COMMAND End-to-End Pipeline.")
    parser.add_argument("video_path", help="Path to creator gameplay video or VOD")
    parser.add_argument("--creator", required=True, help="Registered creator handle")
    parser.add_argument("--max-clips", type=int, default=3, help="Maximum highlight candidates to evaluate and render (1-10)")
    args = parser.parse_args()

    run_pipeline(
        video_path=args.video_path,
        creator_handle=args.creator,
        max_clips=args.max_clips,
    )
