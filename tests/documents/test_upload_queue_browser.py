from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"


def payload(name: str, mime_type: str, body: bytes) -> dict[str, object]:
    return {"name": name, "mimeType": mime_type, "buffer": body}


def test_picker_adds_multiple_manual_category_rows_and_removes_one(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    page.locator("#document-files").set_input_files(
        [
            payload("checking.csv", "text/csv", CSV_BYTES),
            payload("pay.pdf", "application/pdf", b"%PDF-synthetic"),
        ]
    )

    rows = page.locator("#document-queue-body tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).get_by_label("Document category for checking.csv")).to_have_value("")
    expect(rows.nth(1).get_by_label("Document category for pay.pdf")).to_have_value("")
    expect(page.locator("#process-documents")).to_be_disabled()

    rows.nth(0).locator("select").select_option("transaction_statement")
    expect(page.locator("#process-documents")).to_have_text("Process 1 file")
    rows.nth(1).get_by_role("button", name="Remove pay.pdf").click()
    expect(rows).to_have_count(1)

    rows.nth(0).get_by_role("button", name="Remove checking.csv").click()
    expect(rows).to_have_count(0)
    expect(page.locator(".document-queue-wrap")).to_be_hidden()
    expect(page.locator("#document-drop-zone")).to_be_focused()
    expect(page.locator("#document-live-status")).to_contain_text("0 files remain in the queue")


def test_drop_zone_is_the_only_accessible_picker_control(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    drop_zone = page.locator("#document-drop-zone")
    drop_zone.focus()
    page.keyboard.press("Tab")
    focus_moved_to_retention = page.locator('input[name="retention_choice"]').first.evaluate(
        "element => element === document.activeElement"
    )
    accessible_file_inputs = page.get_by_role("button", name="Choose File").count()

    with page.expect_file_chooser() as chooser_info:
        drop_zone.press("Enter")
    chooser_info.value.set_files(payload("keyboard.csv", "text/csv", CSV_BYTES))

    expect(page.get_by_text("keyboard.csv", exact=True)).to_be_visible()
    assert (focus_moved_to_retention, accessible_file_inputs) == (True, 0)


def test_same_file_is_suppressed_and_queue_is_limited_to_ten(
    signed_in_upload_page: tuple[Page, int],
    tmp_path: Path,
) -> None:
    page, _ = signed_in_upload_page
    same = tmp_path / "checking.csv"
    same.write_bytes(CSV_BYTES)
    page.locator("#document-files").set_input_files([same])
    page.locator("#document-files").set_input_files([same])
    expect(page.locator("#document-queue-body tr")).to_have_count(1)
    expect(page.locator("#document-live-status")).to_contain_text("already in the queue")

    page.locator("#document-files").set_input_files(
        [payload(f"file-{number}.csv", "text/csv", CSV_BYTES) for number in range(2, 12)]
    )
    expect(page.locator("#document-queue-body tr")).to_have_count(10)
    expect(page.locator("#document-live-status")).to_contain_text("10-file limit")


def test_drag_and_drop_adds_multiple_files_in_one_drop(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    page.evaluate(
        """(files) => {
          const transfer = new DataTransfer();
          for (const {name, type, body} of files) {
            transfer.items.add(new File([body], name, {type, lastModified: 1722470400000}));
          }
          document.querySelector('#document-drop-zone').dispatchEvent(
            new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true})
          );
        }""",
        [
            {
                "name": "dropped.csv",
                "type": "text/csv",
                "body": "Date,Description,Amount\n",
            },
            {
                "name": "dropped-pay.pdf",
                "type": "application/pdf",
                "body": "%PDF-synthetic",
            },
        ],
    )
    expect(page.locator("#document-queue-body tr")).to_have_count(2)
    expect(page.get_by_text("dropped.csv", exact=True)).to_be_visible()
    expect(page.get_by_text("dropped-pay.pdf", exact=True)).to_be_visible()


def test_manual_categories_control_file_eligibility(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    page.locator("#document-files").set_input_files(
        [
            payload("wrong.pdf", "application/pdf", b"%PDF-wrong"),
            payload("future.pdf", "application/pdf", b"%PDF-future"),
            payload("unlisted.pdf", "application/pdf", b"%PDF-unlisted"),
            payload("large.csv", "text/csv", b"x" * (5 * 1024 * 1024 + 1)),
            payload("notes.txt", "text/plain", b"not supported"),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("brokerage_statement")
    rows.nth(2).locator("select").select_option("unlisted")
    rows.nth(3).locator("select").select_option("transaction_statement")

    expect(rows.nth(0).locator(".document-status")).to_contain_text("does not support")
    expect(rows.nth(1).locator(".document-status")).to_contain_text("not available yet")
    expect(rows.nth(2).locator(".document-status")).to_contain_text("Remove this file")
    expect(rows.nth(3).locator(".document-status")).to_contain_text("5 MiB limit")
    expect(rows.nth(4).locator(".document-status")).to_contain_text(
        "Choose a CSV, PDF, PNG, or JPEG"
    )
    expect(page.locator("#process-documents")).to_be_disabled()


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 800}, {"width": 390, "height": 844}],
)
def test_queue_has_no_page_overflow_at_supported_widths(
    signed_in_upload_page: tuple[Page, int],
    viewport: dict[str, int],
) -> None:
    page, _ = signed_in_upload_page
    page.set_viewport_size(viewport)
    page.locator("#document-files").set_input_files(
        [
            payload("checking.csv", "text/csv", CSV_BYTES),
            payload("pay.pdf", "application/pdf", b"%PDF"),
        ]
    )
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    client_width = page.evaluate("document.documentElement.clientWidth")
    overflowing = page.locator("body *").evaluate_all(
        """(elements, width) => elements
          .map((element) => {
            const rect = element.getBoundingClientRect();
            return {
              selector: `${element.tagName.toLowerCase()}#${element.id}.${element.className}`,
              left: rect.left,
              right: rect.right,
              scrollWidth: element.scrollWidth,
              clientWidth: element.clientWidth,
            };
          })
          .filter((element) => element.left < 0 || element.right > width)""",
        client_width,
    )
    assert scroll_width == client_width, overflowing
