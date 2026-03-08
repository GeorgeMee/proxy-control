Feature Spec: Remote File Upload Card
Goal

Add a new card to the dashboard that uploads text content to a remote server file.

Use scp upload, not ssh heredoc.

UI

New card: Remote File Upload

Fields:

1️⃣ File path

/home/Docs/to-ai.md

Single line input.

2️⃣ Content textarea

initial height ≈120px

auto grow

max height ≈300px

overflow scroll

Button:

Send

Status line under button.

API
POST /api/remote/upload

Request:

{
  "path": "/home/Docs/to-ai.md",
  "content": "text content"
}
Backend Logic

1️⃣ Parse path

directory = dirname(path)
filename = basename(path)

2️⃣ Check remote directory

Run:

ssh user@host "test -d <directory>"

If false:

return error: directory not found

3️⃣ Create temp file

tempfile.NamedTemporaryFile()
write(content)

4️⃣ Upload

scp temp_file user@host:path

Behavior:

overwrite existing file
create file if missing

5️⃣ delete temp file

Response

Success

{ ok: true, message: "uploaded" }

Error

{ ok: false, message: "directory not found" }
Files to Modify
app.py
templates/dashboard.html
static/app.js
README.md
Limits

Content size ≤ 20KB

SSH timeout ≤ 10s

README Update

Add section:

Remote File Upload

Explain:

upload text to remote file

directory must exist

file will be created if missing

Example:

/home/Docs/to-ai.md
Implementation Notes

Use config values:

remote_host
remote_user

Upload command format:

scp temp_file user@host:/path/file