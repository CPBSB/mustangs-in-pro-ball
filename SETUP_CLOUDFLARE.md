# Put Version 5.0 on Cloudflare Pages

This guide assumes you already have a GitHub repository named `mustangs-in-pro-ball`.

## Part 1 — Replace the old GitHub files

1. Download and unzip the Version 5.0 package.
2. Open your GitHub repository.
3. Upload the Version 5.0 files so the repository root contains `index.html`, `app.js`, `styles.css`, `data`, `scripts`, and `.github`.
4. Make sure `.github/workflows` contains:
   - `morning-edition.yml`
   - `live-refresh.yml`
   - `validate.yml`
5. Commit the upload to the `main` branch.

If your browser will not let you upload the hidden `.github` folder, create the workflow files from GitHub using **Add file → Create new file** and type the full filenames above.

## Part 2 — Give GitHub Actions permission to update data

1. In the GitHub repository click **Settings**.
2. In the left menu open **Actions** and click **General**.
3. Scroll to **Workflow permissions**.
4. Choose **Read and write permissions**.
5. Click **Save**.

## Part 3 — Create the Cloudflare Pages site

1. Go to `https://dash.cloudflare.com/` and sign in or create a free account.
2. Open **Workers & Pages**.
3. Choose **Create application**.
4. Choose **Pages**.
5. Choose **Import an existing Git repository** / **Connect to Git**.
6. Connect your GitHub account when Cloudflare asks.
7. Select the repository `mustangs-in-pro-ball`.
8. For the production branch choose `main`.
9. This is a plain static site, so use **no framework preset**.
10. Leave the build command empty.
11. Set the build output directory to the repository root. Depending on the Cloudflare screen this may be `/` or left blank for a no-build static repository.
12. Click **Save and Deploy**.

Cloudflare will give the site an address similar to:

```text
https://mustangs-in-pro-ball.pages.dev
```

## Part 4 — Test the morning update

1. Return to GitHub.
2. Click **Actions** at the top.
3. On the left click **Publish morning edition**.
4. Click **Run workflow**.
5. Click the green **Run workflow** button.
6. Wait for a green check mark.
7. Open the repository **Code** tab and confirm the `data` files have a newer commit time.
8. Cloudflare should detect that commit and publish it automatically.
9. Refresh the `.pages.dev` site.

## Part 5 — Test the 30-minute updater

1. Open GitHub **Actions**.
2. Click **Refresh live baseball data**.
3. Click **Run workflow**.
4. Wait for the green check.

After this, GitHub will attempt this job every 30 minutes automatically.

## What runs when

- `Publish morning edition`: daily around 6:00 AM Pacific during daylight-saving time (5:00 AM Pacific during standard time because GitHub cron schedules use UTC).
- `Refresh live baseball data`: every 30 minutes.
- `Validate site`: whenever code is pushed to `main` or a pull request is opened.

## If Cloudflare does not redeploy after a GitHub commit

Open the Cloudflare Pages project and verify that the GitHub repository is connected and `main` is the production branch. With Git integration, Cloudflare automatically deploys new commits to the connected production branch.

## If a GitHub Action turns red

Click the failed run, then click the red step. Copy the error text or take a screenshot and send it back for troubleshooting.
