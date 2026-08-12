import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import func, select

from app.db.models import Payslip, UploadedFile

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"
STATEMENT_CSV_BYTES = (
    b"account_name,institution,account_last_four,total_balance,as_of_date\n"
    b"Example account,Example institution,1234,12345.67,2026-08-01\n"
)
PDF_BYTES = b"%PDF-synthetic-browser"


def payload(name: str, mime_type: str, body: bytes) -> dict[str, object]:
    return {"name": name, "mimeType": mime_type, "buffer": body}


def fulfill_success(route, next_label: str = "Map columns") -> None:
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "message": "Ready for review.",
                "next_url": "/workspaces/1/imports/1/mapping",
                "next_label": next_label,
            }
        ),
    )


def capture_live_announcements(page: Page) -> None:
    page.evaluate(
        """() => {
          window.documentAnnouncements = [];
          const status = document.querySelector('#document-live-status');
          new MutationObserver(() => {
            window.documentAnnouncements.push(status.textContent);
          }).observe(status, {childList: true, characterData: true, subtree: true});
        }"""
    )


def live_announcements(page: Page) -> list[str]:
    return page.evaluate("window.documentAnnouncements")


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
            payload("statement.pdf", "application/pdf", b"%PDF-statement"),
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
    expect(rows.nth(1).locator(".document-status")).to_contain_text("Ready to process")
    expect(rows.nth(2).locator(".document-status")).to_contain_text("Remove this file")
    expect(rows.nth(3).locator(".document-status")).to_contain_text("5 MiB limit")
    expect(rows.nth(4).locator(".document-status")).to_contain_text(
        "Choose a CSV, PDF, PNG, or JPEG"
    )
    expect(page.locator("#process-documents")).to_have_text("Process 1 file")


def test_ready_files_process_sequentially_and_keep_independent_results(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.method == "POST" and request.url.endswith("/document-uploads")
            else None
        ),
    )
    page.locator("#document-files").set_input_files(
        [
            payload("broken.csv", "text/csv", b"not,a,rectangular\n1,2\n"),
            payload("pay.pdf", "application/pdf", PDF_BYTES),
            payload("balance.csv", "text/csv", STATEMENT_CSV_BYTES),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("payslip")
    rows.nth(2).locator("select").select_option("brokerage_statement")
    capture_live_announcements(page)

    page.locator("#process-documents").click()

    expect(rows.nth(0).locator(".document-status")).to_contain_text("fewer fields than the header")
    expect(rows.nth(1).get_by_role("link", name="Review payslip")).to_be_visible()
    expect(rows.nth(2).get_by_role("link", name="Review balance")).to_be_visible()
    expect(rows.nth(0)).to_have_attribute("aria-busy", "false")
    expect(rows.nth(1)).to_have_attribute("aria-busy", "false")
    expect(rows.nth(2)).to_have_attribute("aria-busy", "false")
    expect(page.locator("#process-documents")).to_have_text("Process 0 files")
    expect(page.locator("#process-documents")).to_be_disabled()
    assert len(requests) == 3
    announcements = live_announcements(page)
    assert any(
        announcement.startswith("broken.csv:") and "fewer fields than the header" in announcement
        for announcement in announcements
    )
    assert "pay.pdf: Ready for review." in announcements
    assert "balance.csv: Ready for review." in announcements
    assert announcements[-1] == "Processing complete. 0 files ready to process."


def test_valid_csv_and_payslip_produce_locked_review_links_and_keep_retention(
    signed_in_upload_page: tuple[Page, int], live_document_app: tuple[str, object]
) -> None:
    page, _ = signed_in_upload_page
    _, factory = live_document_app
    page.locator("#document-files").set_input_files(
        [
            payload("checking.csv", "text/csv", CSV_BYTES),
            payload("pay.pdf", "application/pdf", PDF_BYTES),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("payslip")
    page.get_by_label("Retain each raw document privately").check()
    page.locator("#process-documents").click()

    expect(rows.nth(0).get_by_role("link", name="Map columns")).to_be_visible()
    expect(rows.nth(1).get_by_role("link", name="Review payslip")).to_be_visible()
    expect(rows.nth(0).locator("select")).to_be_disabled()
    expect(rows.nth(1).locator("select")).to_be_disabled()
    expect(rows.nth(0)).to_have_attribute("aria-busy", "false")
    expect(rows.nth(1)).to_have_attribute("aria-busy", "false")
    expect(page.locator("#process-documents")).to_have_text("Process 0 files")
    expect(page.locator("#process-documents")).to_be_disabled()
    with factory() as session:
        uploads = session.scalars(select(UploadedFile).order_by(UploadedFile.id)).all()
        assert [upload.retention_choice for upload in uploads] == ["retain", "retain"]


def test_network_failure_can_retry_only_the_failed_row(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    aborted = False

    def fail_once(route) -> None:
        nonlocal aborted
        if not aborted:
            aborted = True
            route.abort()
        else:
            route.continue_()

    page.route("**/document-uploads", fail_once)
    page.locator("#document-files").set_input_files(payload("checking.csv", "text/csv", CSV_BYTES))
    row = page.locator("#document-queue-body tr")
    row.locator("select").select_option("transaction_statement")
    capture_live_announcements(page)
    page.locator("#process-documents").click()
    expect(row.get_by_role("button", name="Retry checking.csv")).to_be_visible()
    assert any(
        announcement == "checking.csv: The upload was interrupted. Retry this file."
        for announcement in live_announcements(page)
    )
    row.get_by_role("button", name="Retry checking.csv").click()
    expect(row.get_by_role("link", name="Map columns")).to_be_visible()


def test_retry_requests_share_the_global_one_at_a_time_gate(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    initial_failures = 0
    in_flight = 0
    max_in_flight = 0
    pending_routes = []

    def control_requests(route) -> None:
        nonlocal initial_failures, in_flight, max_in_flight
        if initial_failures < 2:
            initial_failures += 1
            route.abort()
            return
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        pending_routes.append(route)

    def release_one() -> None:
        nonlocal in_flight
        route = pending_routes.pop(0)
        in_flight -= 1
        fulfill_success(route)

    page.route("**/document-uploads", control_requests)
    page.locator("#document-files").set_input_files(
        [
            payload("first.csv", "text/csv", CSV_BYTES),
            payload("second.csv", "text/csv", CSV_BYTES.replace(b"Market", b"Store")),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("transaction_statement")
    page.locator("#process-documents").click()
    first_retry = rows.nth(0).get_by_role("button", name="Retry first.csv")
    second_retry = rows.nth(1).get_by_role("button", name="Retry second.csv")
    expect(first_retry).to_be_visible()
    expect(second_retry).to_be_visible()

    with page.expect_request("**/document-uploads"):
        first_retry.click()
    try:
        expect(second_retry).to_be_disabled()
    finally:
        release_one()
    expect(rows.nth(0).get_by_role("link", name="Map columns")).to_be_visible()

    expect(second_retry).to_be_enabled()
    with page.expect_request("**/document-uploads"):
        second_retry.click()
    release_one()
    expect(rows.nth(1).get_by_role("link", name="Map columns")).to_be_visible()
    assert max_in_flight == 1


def test_batch_retention_is_snapshotted_while_requests_are_serialized(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    observed_retention: list[str] = []
    pending_routes = []

    def control_requests(route) -> None:
        body = route.request.post_data_buffer or b""
        match = re.search(rb'name="retention_choice"\r\n\r\n([^\r\n]+)', body)
        assert match is not None
        observed_retention.append(match.group(1).decode("ascii"))
        if len(observed_retention) == 1:
            pending_routes.append(route)
        else:
            fulfill_success(route)

    page.route("**/document-uploads", control_requests)
    page.locator("#document-files").set_input_files(
        [
            payload("first.csv", "text/csv", CSV_BYTES),
            payload("second.csv", "text/csv", CSV_BYTES.replace(b"Market", b"Store")),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("transaction_statement")
    retain = page.get_by_label("Retain each raw document privately")
    delete = page.get_by_label("Delete each raw document")
    retain.check()
    page.locator("#process-documents").click()
    expect(retain).to_be_disabled()
    expect(delete).to_be_disabled()

    page.locator('input[name="retention_choice"]').evaluate_all(
        """radios => {
          radios.find(radio => radio.value === 'retain').checked = false;
          radios.find(radio => radio.value === 'delete_after_import').checked = true;
        }"""
    )
    fulfill_success(pending_routes.pop())

    expect(rows.nth(0).get_by_role("link", name="Map columns")).to_be_visible()
    expect(rows.nth(1).get_by_role("link", name="Map columns")).to_be_visible()
    expect(retain).to_be_enabled()
    expect(delete).to_be_enabled()
    assert observed_retention == ["retain", "retain"]


def test_files_added_during_processing_wait_for_the_next_explicit_batch(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    request_count = 0
    delayed_routes = []

    def delay_first_request(route) -> None:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            delayed_routes.append(route)
        else:
            fulfill_success(route)

    page.route("**/document-uploads", delay_first_request)
    page.locator("#document-files").set_input_files(payload("first.csv", "text/csv", CSV_BYTES))
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")

    with page.expect_request("**/document-uploads"):
        page.locator("#process-documents").click()
    page.locator("#document-files").set_input_files(
        payload("added-later.csv", "text/csv", CSV_BYTES.replace(b"Market", b"Store"))
    )
    expect(rows).to_have_count(2)
    rows.nth(1).locator("select").select_option("transaction_statement")

    fulfill_success(delayed_routes.pop())

    expect(rows.nth(0).get_by_role("link", name="Map columns")).to_be_visible()
    expect(rows.nth(1).locator(".document-status")).to_have_text("Ready to process.")
    expect(page.locator("#process-documents")).to_have_text("Process 1 file")
    assert request_count == 1

    page.locator("#process-documents").click()
    expect(rows.nth(1).get_by_role("link", name="Map columns")).to_be_visible()
    assert request_count == 2


def test_malformed_post_commit_payslip_response_retries_to_the_existing_review_link(
    signed_in_upload_page: tuple[Page, int],
    live_document_app: tuple[str, object],
) -> None:
    page, _ = signed_in_upload_page
    _, factory = live_document_app
    first_next_url = ""
    request_count = 0

    def corrupt_first_committed_response(route) -> None:
        nonlocal first_next_url, request_count
        request_count += 1
        if request_count == 1:
            response = route.fetch()
            first_next_url = response.json()["next_url"]
            route.fulfill(status=200, content_type="application/json", body="{")
        else:
            route.continue_()

    page.route("**/document-uploads", corrupt_first_committed_response)
    page.locator("#document-files").set_input_files(
        payload("pay.pdf", "application/pdf", PDF_BYTES)
    )
    row = page.locator("#document-queue-body tr")
    row.locator("select").select_option("payslip")
    page.locator("#process-documents").click()
    retry = row.get_by_role("button", name="Retry pay.pdf")
    expect(retry).to_be_visible()

    retry.click()

    review_link = row.get_by_role("link", name="Review payslip")
    expect(review_link).to_be_visible()
    expect(review_link).to_have_attribute("href", first_next_url)
    with factory() as session:
        payslip_count = session.scalar(select(func.count(Payslip.id)))
        upload_count = session.scalar(select(func.count(UploadedFile.id)))
        uploaded_file = session.scalar(select(UploadedFile))
    assert request_count == 2
    assert (payslip_count, upload_count) == (1, 1)
    assert uploaded_file is not None
    assert uploaded_file.retention_choice == "delete_after_import"


@pytest.mark.parametrize(
    "malformed_body",
    [
        "null",
        json.dumps({"ok": True, "message": "Ready for review."}),
        json.dumps(
            {
                "ok": True,
                "message": "Ready for review.",
                "next_url": "http://[invalid",
                "next_label": "Map columns",
            }
        ),
    ],
    ids=["non-object", "missing-fields", "invalid-url"],
)
def test_malformed_success_is_retryable_and_does_not_stop_later_rows(
    signed_in_upload_page: tuple[Page, int], malformed_body: str
) -> None:
    page, _ = signed_in_upload_page
    request_count = 0

    def respond(route) -> None:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            route.fulfill(status=200, content_type="application/json", body=malformed_body)
        else:
            fulfill_success(route)

    page.route("**/document-uploads", respond)
    page.locator("#document-files").set_input_files(
        [
            payload("first.csv", "text/csv", CSV_BYTES),
            payload("second.csv", "text/csv", CSV_BYTES.replace(b"Market", b"Store")),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("transaction_statement")
    page.locator("#process-documents").click()

    expect(rows.nth(0).get_by_role("button", name="Retry first.csv")).to_be_visible()
    expect(rows.nth(1).get_by_role("link", name="Map columns")).to_be_visible()
    assert request_count == 2


def test_expired_csrf_stops_before_the_second_ready_file(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.method == "POST" and request.url.endswith("/document-uploads")
            else None
        ),
    )
    page.locator("#document-files").set_input_files(
        [
            payload("first.csv", "text/csv", CSV_BYTES),
            payload("second.csv", "text/csv", CSV_BYTES.replace(b"Market", b"Store")),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("transaction_statement")
    page.locator("#document-csrf-token").evaluate(
        "element => { element.value = 'expired-or-invalid'; }"
    )
    page.locator("#process-documents").click()

    expect(page.locator("#document-page-alert")).to_be_visible()
    expect(rows.nth(1).locator(".document-status")).to_have_text("Ready to process.")
    assert len(requests) == 1


def test_repeated_process_click_starts_only_one_request(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.method == "POST" and request.url.endswith("/document-uploads")
            else None
        ),
    )
    page.locator("#document-files").set_input_files(payload("checking.csv", "text/csv", CSV_BYTES))
    page.locator("#document-queue-body select").select_option("transaction_statement")
    page.locator("#process-documents").evaluate("button => { button.click(); button.click(); }")
    expect(page.get_by_role("link", name="Map columns")).to_be_visible()
    assert len(requests) == 1


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
