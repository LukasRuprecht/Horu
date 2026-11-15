import threading

import pytest
from PIL import Image, ImageDraw, ImageFont

from horu import autocapture
from horu.autocapture import TextWatcher, still_shows
from horu.reads import ReadQueue

FONT = ImageFont.load_default(size=18)


class Screen:
    """Frames the size the watcher shrinks to, with lines of text drawn where asked, and a
    stand-in detector that finds those lines."""

    def __init__(self):
        self.boxes = []

    def frame(self, lines: dict[tuple[int, int], str]) -> Image.Image:
        image = Image.new("RGB", (autocapture.MAP_WIDTH, 270), (30, 40, 60))
        draw = ImageDraw.Draw(image)
        self.boxes = []
        for at, text in lines.items():
            draw.text(at, text, fill=(240, 240, 240), font=FONT)
            self.boxes.append(tuple(int(v) for v in draw.textbbox(at, text, font=FONT)))
        return image

    def detect(self, pixels):
        return list(self.boxes)


def look(lines: dict[tuple[int, int], str], scene: str = "game"):
    """What the watcher saw of a screen with these lines on it."""
    screen = Screen()
    watcher = TextWatcher(screen.detect)
    watcher.update(screen.frame(lines))
    return watcher.seen(scene=scene)


BOX, TAG, CHOICE = (40, 200), (40, 170), (300, 120)


def test_a_newer_look_shows_an_older_ones_text_only_if_all_of_it_is_unchanged():
    line = look({TAG: "Ryuji", BOX: "What was that just now?"})
    # A choice appears beside the line, which stays: reading the newer picture shows both.
    with_choice = look({TAG: "Ryuji", BOX: "What was that just now?", CHOICE: "No idea."})
    assert still_shows(with_choice, line)
    assert not still_shows(line, with_choice)  # the choice is gone again: its picture must be read
    # The next line in the same box: the older picture holds a line the newer one doesn't.
    assert not still_shows(look({TAG: "Ryuji", BOX: "We gotta get out of here!"}), line)
    # The same speaker and box, the line gone.
    assert not still_shows(look({TAG: "Ryuji"}), line)
    # A short line replaced by another short one.
    assert not still_shows(look({BOX: "Huh!?"}), look({BOX: "Hey..."}))


def test_a_line_read_while_typing_out_is_shown_by_the_look_at_the_whole_line():
    typing, whole = look({BOX: "What was"}), look({BOX: "What was that just now?"})
    assert still_shows(whole, typing)
    assert not still_shows(typing, whole)


def test_looks_at_different_scenes_or_picture_sizes_are_never_the_same():
    lines = {BOX: "What was that just now?"}
    assert still_shows(look(lines), look(lines))
    assert not still_shows(look(lines, scene="other regions"), look(lines))
    seen = look(lines)
    smaller = autocapture.Seen(seen.strokes[:200], seen.scene)
    assert not still_shows(smaller, seen)


def test_text_the_detector_missed_counts_all_the_same():
    # Yakuza: the detector found only the name tag on the small picture, not the subtitle under
    # it. A later look without that subtitle must not stand in for it.
    def look_missing(lines):
        screen = Screen()
        watcher = TextWatcher(lambda pixels: [])  # finds nothing
        watcher.update(screen.frame(lines))
        return watcher.seen(scene="game")

    line = look_missing({TAG: "Ryuji", BOX: "What was that just now?"})
    assert not still_shows(look_missing({TAG: "Ryuji"}), line)
    assert still_shows(look_missing({TAG: "Ryuji", BOX: "What was that just now?", CHOICE: "No idea."}), line)


def test_scenery_changing_elsewhere_says_no():
    # It can't be told from a line the detector missed, so the older picture is read too.
    assert not still_shows(look({BOX: "What was that just now?"}), look({BOX: "What was that just now?", (400, 30): "o"}))


def test_a_look_with_no_text_is_shown_by_any_later_one():
    assert still_shows(look({BOX: "Hey..."}), look({}))


def queue_of(**kwargs) -> ReadQueue:
    # Looks are sets of lines here: a newer one covers an older one when it holds all its lines.
    return ReadQueue(covers=lambda newer, older: older <= newer, **kwargs)


def drain(queue: ReadQueue) -> list:
    return [queue.get(timeout=0) for _ in range(len(queue))]


def test_reads_are_done_in_the_order_they_were_set_off():
    queue = queue_of()
    queue.put_auto("first", {"a"})
    queue.put_auto("second", {"b"})
    queue.put_auto("third", {"c"})
    assert drain(queue) == ["first", "second", "third"]


def test_the_hotkey_goes_ahead_of_waiting_automatic_reads():
    queue = queue_of()
    queue.put_auto("auto 1", {"a"})
    queue.put_auto("auto 2", {"b"})
    queue.put(None)  # the hotkey
    queue.put("snip")
    assert drain(queue) == [None, "snip", "auto 1", "auto 2"]


def test_a_waiting_read_is_let_go_when_a_newer_picture_shows_all_its_text():
    queue = queue_of()
    assert queue.put_auto("line typing", {"a"}) == (0, 0)
    assert queue.put_auto("line whole, same strokes", {"a"}) == (1, 0)
    assert queue.put_auto("with a choice", {"a", "choice"}) == (1, 0)
    assert drain(queue) == ["with a choice"]


def test_a_waiting_read_with_a_line_no_newer_picture_has_is_kept():
    queue = queue_of()
    queue.put_auto("short line", {"huh"})
    assert queue.put_auto("next line", {"next"}) == (0, 0)  # the short line has left the screen
    assert queue.put_auto("next line and a choice", {"next", "choice"}) == (1, 0)
    assert drain(queue) == ["short line", "next line and a choice"]


def test_reads_without_a_look_are_never_let_go_for_a_newer_one():
    queue = queue_of()
    queue.put_auto("no look")
    queue.put_auto("look", {"a"})
    queue.put_auto("no look either")
    assert drain(queue) == ["no look", "look", "no look either"]


def test_no_more_than_a_few_pictures_wait_and_the_oldest_goes_first():
    queue = queue_of(most_waiting=3)
    results = [queue.put_auto(n, {n}) for n in range(5)]
    assert results == [(0, 0), (0, 0), (0, 0), (0, 1), (0, 1)]
    queue.put(None)
    assert drain(queue) == [None, 2, 3, 4]  # the hotkey isn't counted or pushed out


def test_a_read_being_done_is_not_let_go():
    queue = queue_of()
    queue.put_auto("being read", {"a"})
    assert queue.get(timeout=0) == "being read"
    assert queue.put_auto("same text", {"a"}) == (0, 0)
    assert drain(queue) == ["same text"]


def test_get_waits_for_a_read():
    queue = queue_of()
    with pytest.raises(TimeoutError):
        queue.get(timeout=0.01)
    got = []
    worker = threading.Thread(target=lambda: got.append(queue.get(timeout=5)))
    worker.start()
    queue.put_auto("read", {"a"})
    worker.join(timeout=5)
    assert got == ["read"]


def test_the_watcher_remembers_its_last_look():
    screen = Screen()
    watcher = TextWatcher(screen.detect)
    assert watcher.seen() is None
    watcher.update(screen.frame({BOX: "What was that just now?"}))
    seen = watcher.seen(scene="game")
    assert seen.scene == "game" and seen.strokes.any()
