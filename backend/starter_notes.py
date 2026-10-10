"""Notes every new account starts with, so a new student can try asking a question right away."""
import logging
import threading

logger = logging.getLogger(__name__)

STARTER_FOLDER = "Getting started"

WELCOME = """# Welcome to NoteScanner

NoteScanner turns your course notes into a study assistant. This folder is just an example;
delete it whenever you like.

## How to use it

1. **Upload your notes.** Use the upload button at the top of the file list, or drag files onto a
   folder. PDFs, photos of handwritten pages and text files all work. Make one folder per course.
2. **Ask questions.** Type in the chat on the right. Answers come from your own notes first, and
   each answer lists the notes it used; click one to open it.
3. **Make flashcards and practice questions.** Open a file, then use Flashcards, Mock MCQ or Summary.
4. **Review a little every day.** Save flashcards as a deck. The Review tab shows the cards that are
   due today, so you revise each card just before you'd forget it.

## Tips

- If a scanned page was read wrongly, open the file, switch to Text view and fix it.
- Mark your most reliable file in a course as the "main source" (right-click it).
- Try asking: "What is Newton's second law?" or "Explain momentum with an example."
"""

SAMPLE_PHYSICS = """# Newton's laws of motion (sample notes)

## First law (inertia)
An object stays at rest, or keeps moving in a straight line at constant speed, unless a net
external force acts on it. Mass is a measure of an object's inertia.

## Second law
The net force on an object equals its mass times its acceleration: $F = ma$.
Force is measured in newtons (N), where 1 N = 1 kg·m/s².

## Third law
For every action there is an equal and opposite reaction: if body A pushes body B, then B pushes A
with a force of the same size in the opposite direction.

## Momentum
Momentum is mass times velocity: $p = mv$. Newton's second law can also be written as
$F = \\frac{dp}{dt}$. In a closed system with no external force, total momentum is conserved.

## Worked example
A 2 kg cart accelerates at 3 m/s². The net force on it is 2 × 3 = 6 N.
"""

STARTER_FILES = (
    ("Welcome to NoteScanner.md", WELCOME),
    ("Sample - Newton's laws of motion.md", SAMPLE_PHYSICS),
)

# The name the student signed up under: "Study AI" is NoteScanner inside Interview.ai's Study AI tab.
APP_NAMES = ("NoteScanner", "Study AI")


def starter_files(app_name: str = "NoteScanner") -> tuple[tuple[str, str], ...]:
    app = app_name if app_name in APP_NAMES else "NoteScanner"
    if app == "NoteScanner":
        return STARTER_FILES
    return ((f"Welcome to {app}.md", WELCOME.replace("NoteScanner", app)), *STARTER_FILES[1:])


def add_starter_notes(user_id: str, app_name: str = "NoteScanner") -> None:
    """Save the starter files for a new account (no AI calls; only local embeddings)."""
    from backend.uploads import save_note_text

    for name, text in starter_files(app_name):
        try:
            save_note_text(user_id, STARTER_FOLDER, name, text)
        except Exception:
            logger.exception("Could not add starter note %s for %s", name, user_id)


def add_starter_notes_in_background(user_id: str, app_name: str = "NoteScanner") -> None:
    threading.Thread(
        target=add_starter_notes, args=(user_id, app_name), name="starter-notes", daemon=True
    ).start()
