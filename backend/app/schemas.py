from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    tier: str
    is_admin: bool = False
    limits: dict = {}
    github_login: str | None = None


class JobCreate(BaseModel):
    repo_url: str = Field(max_length=500)


class JobAccepted(BaseModel):
    job_id: str
    status: str
    status_url: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    progress: int
    queue_reason: str | None = None
    repo_url: str
    repo_name: str
    private: bool = False
    trace_id: str | None = None
    commit_sha: str | None
    summary: dict | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_path: str
    line_start: int
    line_end: int
    severity: str
    category: str
    agent: str
    title: str
    description: str
    suggestion: str | None
    rule_ref: str | None
    source: str = "static"
    prompt_version: str | None = None
    my_vote: int | None = None
    finding_id: int | None = None
    finding_status: str | None = None


class FeedbackIn(BaseModel):
    vote: int = Field(description="+1 helpful, -1 not helpful")
    comment: str | None = Field(default=None, max_length=2000)


class FindingStatusIn(BaseModel):
    status: str = Field(pattern="^(open|dismissed)$")


class PromptVersionIn(BaseModel):
    version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,40}$")
    system: str = Field(min_length=10, max_length=50_000)
    user_template: str = Field(min_length=5, max_length=20_000)
    params: dict = {}
    activate: bool = False


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int = Field(validation_alias="id")
    agent: str
    level: str
    message: str
    progress: int | None
    ts: datetime = Field(validation_alias="created_at")


class TreeEntry(BaseModel):
    name: str
    path: str
    type: str
    size: int = 0
    issue_count: int = 0


class FileOut(BaseModel):
    path: str
    language: str
    content: str
    issues: list[IssueOut]
