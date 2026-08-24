"""POST /billing/student-application.

Contract (from ``lib/UI/screen/billing/student_application_screen.dart`` via
``lib/services/prism_cloud_service.dart``): multipart fields ``email``,
``fullName``, ``institution``, optional ``studentId`` and optional ``proof``
file. Saves the application to MySQL with ``status='pending'`` -- no
verification service exists, so this is purely a durable record of what the
user submitted (see ``models.StudentApplication``). The client only marks its
local "pending" status after this call actually succeeds.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..logging_config import get_logger
from ..models import StudentApplication
from ..schemas import StudentApplicationResponse
from ..security import is_valid_email, normalize_email
from ..storage import is_within, student_proof_dir

log = get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post(
    "/student-application",
    response_model=StudentApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_student_application(
    email: str = Form(...),
    fullName: str = Form(...),
    institution: str = Form(...),
    studentId: str | None = Form(default=None),
    proof: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> StudentApplicationResponse:
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address."
        )

    name = fullName.strip()
    school = institution.strip()
    if not name or not school:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full name and institution are required.",
        )

    application_id = str(uuid.uuid4())
    proof_path: str | None = None

    if proof is not None and proof.filename:
        data = proof.file.read(settings.max_upload_bytes + 1)
        proof.file.close()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="That proof file is too large.",
            )
        if data:
            suffix = "".join(ch for ch in (proof.filename.rsplit(".", 1)[-1:] or [""])[0] if ch.isalnum()) or "bin"
            target = student_proof_dir() / f"{application_id}.{suffix}"
            if is_within(target, student_proof_dir()):
                target.write_bytes(data)
                proof_path = str(target)

    db.add(
        StudentApplication(
            id=application_id,
            user_email=normalized,
            full_name=name,
            institution=school,
            student_id=(studentId or "").strip() or None,
            proof_path=proof_path,
            status="pending",
            created_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        )
    )
    db.flush()

    log.info("Student application %s submitted by %s", application_id, normalized)

    return StudentApplicationResponse(
        id=application_id,
        status="pending",
        message="Application submitted — it's pending review.",
    )
