import { expect, request as playwrightRequest, test, type Page } from "@playwright/test";

const email = process.env.E2E_EMAIL;
const password = process.env.E2E_PASSWORD;
const outsiderEmail = process.env.E2E_EMAIL_2;
const outsiderPassword = process.env.E2E_PASSWORD_2;

async function signIn(page: Page) {
  if (!email || !password) throw new Error("Set E2E_EMAIL and E2E_PASSWORD to a disposable test account.");
  await page.goto("/");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录资料库" }).click();
  await expect(page.getByRole("button", { name: "求职资料包" })).toBeVisible();
}

async function expectNoPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content, `page overflowed ${dimensions.viewport}px viewport`).toBeLessThanOrEqual(
    dimensions.viewport,
  );
}

test("private file-to-export workflow and responsive pack screens", async ({ page, request }) => {
  test.setTimeout(180_000);
  test.skip(test.info().project.name === "edge-mobile-375", "run full file transfer on desktop Edge; mobile project covers layout");
  await signIn(page);
  await expectNoPageOverflow(page);

  const unique = Array.from({ length: 14 }, () => String.fromCharCode(97 + Math.floor(Math.random() * 26))).join("");
  const fileName = `m9-e2e-${unique}.txt`;
  const searchablePhrase = `marker${unique}`;
  const uploadResponse = page.waitForResponse(
    (response) => response.url().includes("/api/v1/documents") && response.request().method() === "POST",
  );
  await page.locator('input[type="file"]').first().setInputFiles({
    name: fileName,
    mimeType: "text/plain",
    buffer: Buffer.from(`Synthetic FileMind M9 document. ${searchablePhrase}.`),
  });
  const uploaded = await uploadResponse;
  expect([200, 202]).toContain(uploaded.status());
  const versionId = (await uploaded.json()).data.version.id as string;

  const csrfProbe = await page.evaluate(async () => {
    const response = await fetch("/api/v1/settings", {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_enabled: true }),
    });
    return response.status;
  });
  expect(csrfProbe).toBe(403);

  if (!outsiderEmail || !outsiderPassword) {
    throw new Error("Set E2E_EMAIL_2 and E2E_PASSWORD_2 to a second disposable account.");
  }
  const outsider = await playwrightRequest.newContext({ baseURL: "http://127.0.0.1:5173" });
  try {
    const login = await outsider.post("/api/v1/auth/login", {
      headers: { Origin: "http://127.0.0.1:5173" },
      data: { email: outsiderEmail, password: outsiderPassword },
    });
    expect(login.status()).toBe(200);
    const privateVersion = await outsider.get(`/api/v1/versions/${versionId}/download`);
    expect(privateVersion.status()).toBe(404);
  } finally {
    await outsider.dispose();
  }

  const documentButton = page.getByRole("button", { name: fileName, exact: true });
  await expect(documentButton).toBeVisible({ timeout: 30_000 });
  const row = page.locator(".document-row").filter({ has: documentButton });
  await expect(row.locator(".doc-status")).toContainText("已解析并可检索", { timeout: 120_000 });

  const search = page.getByRole("textbox", { name: "搜索文件名或正文" });
  await search.fill(searchablePhrase);
  await page.getByRole("button", { name: "全部文件" }).click();
  await search.press("Enter");
  await expect(page.getByText(/1 条正文结果/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: new RegExp(fileName) })).toBeVisible();

  await page.getByRole("button", { name: "求职资料包" }).click();
  await page.getByRole("button", { name: /手动导出/ }).click();
  await expectNoPageOverflow(page);
  await page.getByLabel(new RegExp(fileName)).check();
  await page.getByRole("button", { name: "创建手动导出" }).click();
  const readyExport = page.getByText("资料包已就绪").first();
  await expect(readyExport).toBeVisible({ timeout: 60_000 });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载" }).first().click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^FileMind-[a-f0-9]+\.zip$/);

  await page.getByRole("button", { name: "返回资料库" }).click();
  await page.getByRole("button", { name: fileName, exact: true }).click();
  await page.getByRole("button", { name: "移入回收站" }).click();
  await page.getByRole("button", { name: "求职资料包" }).click();
  await page.getByRole("button", { name: "刷新导出记录" }).click();
  await expect(page.getByText("源文件变化，下载已失效")).toBeVisible({ timeout: 30_000 });
  await expectNoPageOverflow(page);

  const unauthenticated = await request.get("/api/v1/exports");
  expect(unauthenticated.status()).toBe(401);
});

test("login, library, and pack screens fit the configured viewport", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录你的资料库" })).toBeVisible();
  await expectNoPageOverflow(page);
  await signIn(page);
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "求职资料包" }).click();
  await page.getByRole("button", { name: /手动导出/ }).click();
  await expectNoPageOverflow(page);
});
