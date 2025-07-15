
### Question: Describe the race condition in the Gotenberg PDF conversion service in the 'G0tchaberg' challenge. How were concurrent requests with different `waitDelay` parameters used to leak the temporary file path of the PDF?
---
<br>

**RAG-Augmented Answer:**

Based on the provided context, here is a concise answer:

The race condition in the 'G0tchaberg' challenge was that the temporary `index.html` file containing the flag was deleted immediately after its PDF conversion. While its path could be leaked via `chrome://history`, the path only became available *after* the file was already deleted, making a simple two-step "leak-then-read" attack impossible.

To overcome this, the exploit used concurrent requests with different `waitDelay` parameters to manipulate the server's processing queue:

1.  **Path Leaker Request:** A request was sent with a short `waitDelay` (e.g., 6s). Its HTML payload used `setTimeout` to wait until the automated flag submission created its temporary directory. It then rendered a directory listing (`file:///tmp/...`) to leak the name of the flag's temporary sub-directory.

2.  **File Reader Request:** A second request was sent almost simultaneously with a longer `waitDelay` (e.g., 15s). This request was queued after the flag submission, and its long delay prevented the flag's temporary file from being deleted.

While the "File Reader" was waiting, the attacker extracted the directory name from the "Path Leaker's" output, constructed the full file path to the flag, and fed it (via an external server) to the waiting "File Reader" request, which then successfully read the flag file.

**Sources Retrieved by RAG:**
*   Document ID: `40075`
\n**For more details, you can read the full write-ups:**\n*   [Write-up for CTFtime ID 40075](http://ctftime.org/writeup/40075)\n
<br>

---
**Standard LLM Answer (No RAG):**

Of course. The race condition in the Gotenberg PDF conversion service, exploited in the "G0tchaberg" challenge, is a classic example of a Time-of-Check to Time-of-Use (TOCTOU) vulnerability. It allowed an attacker to leak temporary file paths, which could then be leveraged to read arbitrary files from the server's filesystem.

Here is a detailed breakdown of the race condition and the exploit method.

### 1. The Normal Gotenberg Workflow

To understand the vulnerability, we first need to understand the intended process for converting HTML to a PDF:

1.  **Request Received:** A user sends a POST request to an endpoint like `/forms/chromium/convert/html` with the HTML content to be converted (e.g., in a file named `index.html`).
2.  **Temp Directory Creation:** Gotenberg creates a temporary working directory on the server. The path to this directory was predictable, typically following a pattern like `/tmp/gotenberg_xxxxxxxxxxxx/`, where `xxxxxxxxxxxx` was a Unix timestamp with nanosecond precision.
3.  **File Creation:** Gotenberg writes the user-provided `index.html` file into this temporary directory.
4.  **PDF Conversion:** Gotenberg invokes a headless Chromium browser instance, instructing it to open the local file (`/tmp/gotenberg_xxxxxxxxxxxx/index.html`) and print it to a PDF.
5.  **`waitDelay` Parameter:** The user can provide a `waitDelay` parameter (e.g., `waitDelay=5s`). This instructs Chromium to wait for the specified duration *after* the page has loaded but *before* printing it to PDF. This is a legitimate feature designed to allow complex JavaScript-driven pages time to fully render their content.
6.  **Cleanup:** After the PDF is generated and sent back to the user, Gotenberg deletes the entire temporary directory and its contents.

### 2. The Race Condition Vulnerability

The vulnerability lies in the gap between the creation of the temporary directory and its final deletion, combined with its predictable naming scheme.

*   **Predictable Path:** The directory name was based on a high-precision timestamp. While guessing the exact nanosecond is impossible, sending two requests in quick succession would result in two temporary directories with very similar timestamps. For example:
    *   `/tmp/gotenberg_1678886400123456789/`
    *   `/tmp/gotenberg_1678886400123556789/`
*   **The Race Window:** The `waitDelay` parameter is the key to exploiting this. By setting a long `waitDelay`, an attacker could force Gotenberg to keep a temporary directory and its files "alive" for an extended period. This widens the "race window" from milliseconds to many seconds, giving the attacker ample time to act.

### 3. How Concurrent Requests Were Used to Leak the Path

The goal of the first stage of the attack is to force the server to reveal the full path of one of its temporary directories. This was achieved by cleverly using two concurrent requests with different `waitDelay` parameters.

#### Request 1: The "Decoy" Request (to create a long-lived directory)

This request is designed to create a temporary directory and keep it open for a long time.

*   **Endpoint:** `/forms/chromium/convert/html`
*   **Content:** A simple `index.html` file.
*   **Key Parameter:** A very long `waitDelay`, for example, `waitDelay=30s`.

**What happens on the server:**
1.  Gotenberg creates a directory, let's call it `Path_A` (e.g., `/tmp/gotenberg_1678886400000000000/`).
2.  It writes `index.html` into `Path_A`.
3.  It tells Chromium to open the file and then wait for 30 seconds.
4.  For the next 30 seconds, `Path_A` exists on the server's filesystem.

#### Request 2: The "Leaker" Request (to trigger a revealing error)

This request is sent *immediately* after Request 1. Its purpose is to fail in a way that discloses its own temporary path.

*   **Endpoint:** `/forms/chromium/convert/html`
*   **Content:** An `index.html` file that references a non-existent local resource. A more direct method is to ask Gotenberg to convert a local file that doesn't exist by setting the `Gotenberg-Chromium-Convert-Url` header to a `file:///` path that is invalid.
*   **Key Parameter:** A very short or non-existent `waitDelay` to ensure it executes and fails quickly.

**What happens on the server:**
1.  Immediately after processing Request 1, Gotenberg handles Request 2. It creates a new directory, `Path_B` (e.g., `/tmp/gotenberg_1678886400100000000/`). Notice its timestamp is very close to `Path_A`.
2.  It then tries to process the request, which involves accessing a non-existent file.
3.  The operation fails, and Gotenberg returns a **500 Internal Server Error**.
4.  **Crucially, the error message in the response body includes the full path where the error occurred.** The message would look something like: `error: failed to process request: could not find file '/nonexistent.html' in temporary directory: /tmp/gotenberg_1678886400100000000/`.

#### The Result: Path Leak and Prediction

The attacker now has the exact path of the temporary directory from Request 2 (`Path_B`). Because it was created milliseconds after Request 1, the attacker can now accurately **predict the path of the still-existing directory from Request 1 (`Path_A`)**. They can simply decrement the timestamp from the leaked path by a small amount and brute-force the few possible variations.

### 4. From Path Leak to Arbitrary File Read

Once the attacker can predict the temporary path for a request they are about to send, they can achieve arbitrary file read.

1.  The attacker uses the two-request trick described above to leak a path, allowing them to predict the path for their *next* request (`Path_C`).
2.  They send a **third request** to an endpoint that allows including local files, such as the HTML or Markdown converter.
3.  The payload for this third request will contain a reference to a sensitive file on the server using the `file://` protocol. For example, the `index.html` could contain:
    ```html
    <!-- This will render the content of /etc/passwd into the PDF -->
    <iframe src="file:///etc/passwd" width="100%" height="800px"></iframe>
    ```
4.  Gotenberg creates the temporary directory `Path_C` (which the attacker predicted), writes this malicious `index.html` into it, and passes it to Chromium.
5.  Chromium, when rendering the HTML, will access `file:///etc/passwd` on the local filesystem and embed its contents into the iframe.
6.  The final PDF, which is returned to the attacker, now contains the contents of the `/etc/passwd` file.

In summary, the race condition was created by the `waitDelay` parameter, which extended the life of a predictably named temporary directory. This window was exploited by a second, fast-failing request that leaked its own temporary path in an error message, allowing the attacker to predict paths and ultimately trick the service into reading and returning arbitrary local files.

---
