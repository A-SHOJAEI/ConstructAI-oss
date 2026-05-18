"""
Generate synthetic meeting audio for testing the transcription service.

Uses pyttsx3 (offline TTS) to create a short mock safety meeting recording.
Falls back to creating a silent WAV file if TTS is unavailable.

Usage:
    python -m demo.assets.audio.generate_test_audio [output_path]
"""
import struct
import sys
import wave
from pathlib import Path


def generate_silent_wav(output_path: Path, duration_seconds: int = 30, sample_rate: int = 16000) -> Path:
    """Generate a silent WAV file as placeholder."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = sample_rate * duration_seconds

    with wave.open(str(output_path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        # Write near-silence with tiny noise to avoid zero-detection issues
        import random
        random.seed(42)
        for _ in range(num_samples):
            sample = random.randint(-10, 10)
            wav.writeframes(struct.pack("<h", sample))

    return output_path


def generate_test_audio(output_path: Path) -> Path:
    """Generate test audio with TTS if available, otherwise silent WAV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 150)

        script = (
            "Good morning everyone. Welcome to the weekly safety meeting for the "
            "Riverside Mixed-Use Development project. "
            "This week we had two priority one safety alerts. "
            "The first was a crane zone breach detected by our AI camera system. "
            "A worker entered the exclusion zone during an active lift operation. "
            "The system detected this in under two seconds and sent immediate notifications. "
            "The crane operator was alerted and operations were halted. "
            "The second alert was a potential fall detected at the rooftop perimeter. "
            "Our pose estimation model identified a worker near an unguarded edge. "
            "Both incidents were resolved without injury. "
            "Overall, our PPE compliance has improved from 82 percent to 89 percent "
            "since the camera system was activated 30 days ago. "
            "Let's keep up the good work. Safety first."
        )

        engine.save_to_file(script, str(output_path))
        engine.runAndWait()
        print(f"Generated TTS audio: {output_path}")

    except (ImportError, Exception) as e:
        print(f"TTS unavailable ({e}), generating silent WAV placeholder...")
        output_path = output_path.with_suffix(".wav")
        generate_silent_wav(output_path, duration_seconds=60)
        print(f"Generated placeholder: {output_path}")

    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/safety_meeting.wav")
    generate_test_audio(out)
