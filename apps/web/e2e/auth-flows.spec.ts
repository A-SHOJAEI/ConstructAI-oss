import { test, expect } from '@playwright/test'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const API_BASE = 'http://localhost:8000'

/**
 * Build a structurally-valid JWT whose `exp` is in the future so the
 * Next.js middleware accepts it (it checks format + expiry, not signature).
 */
function makeFakeJwt(extraMinutes = 30): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  const payload = btoa(
    JSON.stringify({
      sub: 'test-user-id',
      exp: Math.floor(Date.now() / 1000) + extraMinutes * 60,
    })
  )
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  const sig = btoa('fake-signature')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  return `${header}.${payload}.${sig}`
}

// ---------------------------------------------------------------------------
// Password Login
// ---------------------------------------------------------------------------

test.describe('Password Login', () => {
  test('successful login redirects to /projects', async ({ page }) => {
    // Mock login API to return success with httpOnly cookie
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })
  })

  test('invalid credentials shows error', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 401,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detail: 'Invalid credentials' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('wrong@example.com')
    await page.getByLabel(/password/i).fill('wrong')
    await page.getByRole('button', { name: /sign in/i }).click()

    await expect(page.locator('#login-error')).toBeVisible()
    await expect(page.locator('#login-error')).toContainText('Invalid credentials')
  })

  test('generic server error shows fallback message', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({ status: 500, body: 'Internal Server Error' })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // The catch block falls back to "Login failed" when JSON parsing fails
    await expect(page.locator('#login-error')).toBeVisible()
    await expect(page.locator('#login-error')).toContainText('Login failed')
  })

  test('no tokens stored in sessionStorage or localStorage', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // Wait for navigation to complete
    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })

    // SECURITY [H-07]: tokens must NEVER appear in client-accessible storage
    const ssToken = await page.evaluate(() => sessionStorage.getItem('access_token'))
    const lsToken = await page.evaluate(() => localStorage.getItem('access_token'))
    const ssRefresh = await page.evaluate(() => sessionStorage.getItem('refresh_token'))
    const lsRefresh = await page.evaluate(() => localStorage.getItem('refresh_token'))
    expect(ssToken).toBeNull()
    expect(lsToken).toBeNull()
    expect(ssRefresh).toBeNull()
    expect(lsRefresh).toBeNull()
  })

  test('submit button is disabled while loading', async ({ page }) => {
    // Use a delayed response to observe the loading state
    await page.route(`${API_BASE}/api/v1/auth/login`, async (route) => {
      await new Promise((r) => setTimeout(r, 1500))
      await route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    })

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // Button text changes to "Signing in..." and becomes disabled
    await expect(page.getByRole('button', { name: /signing in/i })).toBeDisabled()
  })

  test('email field uses type=email for validation', async ({ page }) => {
    await page.goto('/login')
    const emailInput = page.locator('#email')
    await expect(emailInput).toHaveAttribute('type', 'email')
    await expect(emailInput).toHaveAttribute('required', '')
  })
})

// ---------------------------------------------------------------------------
// MFA Login
// ---------------------------------------------------------------------------

test.describe('MFA Login', () => {
  test('MFA required shows code input', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'challenge-jwt' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('mfa@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // MFA form should appear with the code input
    await expect(page.getByLabel(/mfa code/i)).toBeVisible({ timeout: 5000 })
    // Verify the helper text is shown
    await expect(page.getByText(/6-digit code/i)).toBeVisible()
    // Verify the Verify button exists
    await expect(page.getByRole('button', { name: /verify/i })).toBeVisible()
  })

  test('MFA code submission completes login', async ({ page }) => {
    // Step 1: login returns MFA required
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'challenge-jwt' }),
      })
    )

    // Step 2: MFA verify returns success with httpOnly cookie
    await page.route(`${API_BASE}/api/v1/auth/mfa/verify`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('mfa@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // Wait for MFA form and fill code
    const mfaInput = page.getByLabel(/mfa code/i)
    await mfaInput.waitFor({ timeout: 5000 })
    await mfaInput.fill('123456')

    // Submit MFA code
    await page.getByRole('button', { name: /verify/i }).click()
    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })
  })

  test('MFA verify failure shows error', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'challenge-jwt' }),
      })
    )

    await page.route(`${API_BASE}/api/v1/auth/mfa/verify`, (route) =>
      route.fulfill({
        status: 401,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detail: 'Invalid MFA code' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('mfa@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    const mfaInput = page.getByLabel(/mfa code/i)
    await mfaInput.waitFor({ timeout: 5000 })
    await mfaInput.fill('000000')
    await page.getByRole('button', { name: /verify/i }).click()

    await expect(page.locator('#login-error')).toBeVisible()
    await expect(page.locator('#login-error')).toContainText('Invalid MFA code')
  })

  test('MFA verify button disabled until 6 digits entered', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'challenge-jwt' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('mfa@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    const mfaInput = page.getByLabel(/mfa code/i)
    await mfaInput.waitFor({ timeout: 5000 })

    // With fewer than 6 digits, Verify button should be disabled
    await mfaInput.fill('123')
    await expect(page.getByRole('button', { name: /verify/i })).toBeDisabled()

    // With 6 digits, Verify button should be enabled
    await mfaInput.fill('123456')
    await expect(page.getByRole('button', { name: /verify/i })).toBeEnabled()
  })

  test('MFA input has numeric inputMode and maxLength 6', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'challenge-jwt' }),
      })
    )

    await page.goto('/login')
    await page.getByLabel(/email/i).fill('mfa@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    const mfaInput = page.locator('#mfa-code')
    await mfaInput.waitFor({ timeout: 5000 })
    await expect(mfaInput).toHaveAttribute('inputmode', 'numeric')
    await expect(mfaInput).toHaveAttribute('maxlength', '6')
  })
})

// ---------------------------------------------------------------------------
// SSO Login
// ---------------------------------------------------------------------------

test.describe('SSO Login', () => {
  test('SSO success fragment triggers exchange and redirect', async ({ page }) => {
    // Mock the SSO code exchange endpoint
    await page.route(`${API_BASE}/api/v1/auth/sso/exchange`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    // Navigate to login with SSO success hash fragment
    await page.goto('/login#sso=success&code=test-auth-code')
    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })
  })

  test('SSO exchange failure shows error', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/sso/exchange`, (route) =>
      route.fulfill({
        status: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detail: 'Invalid code' }),
      })
    )

    await page.goto('/login#sso=success&code=bad-code')
    await expect(page.locator('#login-error')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('#login-error')).toContainText('SSO sign-in failed')
  })

  test('SSO MFA fragment exchanges code and shows MFA form', async ({ page }) => {
    // SSO MFA flow: exchange the opaque mfa_token code for a real MFA token
    await page.route(`${API_BASE}/api/v1/auth/sso/exchange`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'real-mfa-jwt' }),
      })
    )

    await page.goto('/login#sso=mfa_required&mfa_token=sso-mfa-challenge')

    // Should show MFA input after exchange
    await expect(page.getByLabel(/mfa code/i)).toBeVisible({ timeout: 5000 })
  })

  test('SSO MFA complete flow: exchange, code entry, verify', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/sso/exchange`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_required: true, mfa_token: 'real-mfa-jwt' }),
      })
    )
    await page.route(`${API_BASE}/api/v1/auth/mfa/verify`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login#sso=mfa_required&mfa_token=sso-mfa-challenge')

    const mfaInput = page.getByLabel(/mfa code/i)
    await mfaInput.waitFor({ timeout: 5000 })
    await mfaInput.fill('654321')
    await page.getByRole('button', { name: /verify/i }).click()

    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })
  })

  test('SSO email_unverified fragment shows verification error', async ({ page }) => {
    await page.goto('/login#sso=email_unverified')

    await expect(page.locator('#login-error')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('#login-error')).toContainText('Email verification required')
  })

  test('SSO fragment is cleared from URL to prevent leakage', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/sso/exchange`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login#sso=success&code=secret-code')

    // The useEffect calls replaceState to clear the hash immediately
    // Wait briefly then check the fragment is gone (before redirect completes)
    await page.waitForFunction(() => !window.location.hash.includes('code='))
  })

  test('Google SSO button initiates authorization', async ({ page }) => {
    let authorizeCalled = false
    await page.route(`${API_BASE}/api/v1/auth/sso/google/authorize`, (route) => {
      authorizeCalled = true
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          authorize_url: 'https://accounts.google.com/o/oauth2/v2/auth?client_id=fake',
        }),
      })
    })

    await page.goto('/login')
    await page.getByRole('button', { name: /google/i }).click()

    // Verify the authorize endpoint was called
    await page.waitForTimeout(500)
    expect(authorizeCalled).toBe(true)
  })

  test('Microsoft SSO button initiates authorization', async ({ page }) => {
    let authorizeCalled = false
    await page.route(`${API_BASE}/api/v1/auth/sso/microsoft/authorize`, (route) => {
      authorizeCalled = true
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          authorize_url: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=fake',
        }),
      })
    })

    await page.goto('/login')
    await page.getByRole('button', { name: /microsoft/i }).click()

    await page.waitForTimeout(500)
    expect(authorizeCalled).toBe(true)
  })

  test('SSO rejects authorize URL with untrusted host', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/sso/google/authorize`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          authorize_url: 'https://evil.example.com/phish',
        }),
      })
    )

    await page.goto('/login')
    await page.getByRole('button', { name: /google/i }).click()

    await expect(page.locator('#login-error')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('#login-error')).toContainText('Invalid SSO redirect URL')
  })
})

// ---------------------------------------------------------------------------
// Auth Redirect (Middleware)
// ---------------------------------------------------------------------------

test.describe('Auth Redirect', () => {
  test('unauthenticated user visiting /projects is redirected to /login', async ({ page }) => {
    // No cookies set -- middleware should redirect
    await page.goto('/projects')
    await expect(page).toHaveURL(/\/login/)
  })

  test('redirect preserves original path in query param', async ({ page }) => {
    await page.goto('/documents')
    await expect(page).toHaveURL(/\/login\?redirect=%2Fdocuments/)
  })

  test('redirect preserves nested path', async ({ page }) => {
    await page.goto('/projects/123/schedule')
    await expect(page).toHaveURL(/\/login\?redirect=%2Fprojects%2F123%2Fschedule/)
  })

  test('public paths are accessible without auth', async ({ page }) => {
    // / and /login are public per middleware PUBLIC_PATHS
    const response = await page.goto('/login')
    // Should NOT redirect -- we stay on /login
    expect(page.url()).toContain('/login')
    expect(response?.status()).toBe(200)
  })

  test('forgot-password is accessible without auth', async ({ page }) => {
    const response = await page.goto('/forgot-password')
    expect(page.url()).toContain('/forgot-password')
    // Should not redirect to /login
    expect(page.url()).not.toContain('/login?redirect')
  })

  test('expired JWT in cookie triggers redirect', async ({ page }) => {
    // Create a JWT that expired in the past
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    const payload = btoa(
      JSON.stringify({ sub: 'test', exp: Math.floor(Date.now() / 1000) - 3600 })
    )
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    const sig = btoa('sig')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    const expiredJwt = `${header}.${payload}.${sig}`

    await page.context().addCookies([
      { name: 'access_token', value: expiredJwt, domain: 'localhost', path: '/' },
    ])

    await page.goto('/projects')
    await expect(page).toHaveURL(/\/login/)
  })

  test('valid JWT in cookie allows access to protected route', async ({ page }) => {
    const validJwt = makeFakeJwt(30)

    await page.context().addCookies([
      { name: 'access_token', value: validJwt, domain: 'localhost', path: '/' },
    ])

    // Mock the /auth/me call that AuthProvider makes on load
    await page.route(`${API_BASE}/api/v1/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: 'user-1',
          email: 'test@example.com',
          full_name: 'Test User',
          role: 'admin',
          org_id: 'org-1',
        }),
      })
    )

    const response = await page.goto('/projects')
    // Middleware should NOT redirect -- we stay on /projects
    expect(page.url()).toContain('/projects')
    // Should not have been redirected to /login
    expect(page.url()).not.toContain('/login')
  })

  test('malformed token triggers redirect', async ({ page }) => {
    await page.context().addCookies([
      { name: 'access_token', value: 'not-a-jwt', domain: 'localhost', path: '/' },
    ])

    await page.goto('/projects')
    await expect(page).toHaveURL(/\/login/)
  })
})

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

test.describe('Logout', () => {
  test('logout API is called and user returns to /login', async ({ page }) => {
    const validJwt = makeFakeJwt(30)
    let logoutCalled = false

    await page.context().addCookies([
      { name: 'access_token', value: validJwt, domain: 'localhost', path: '/' },
    ])

    // Mock /auth/me so the app thinks user is logged in
    await page.route(`${API_BASE}/api/v1/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: 'user-1',
          email: 'test@example.com',
          full_name: 'Test User',
          role: 'admin',
          org_id: 'org-1',
        }),
      })
    )

    // Mock logout endpoint
    await page.route(`${API_BASE}/api/v1/auth/logout`, (route) => {
      logoutCalled = true
      return route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': 'access_token=; Max-Age=0; Path=/',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Logged out' }),
      })
    })

    // Mock refresh endpoint (AuthProvider may call it)
    await page.route(`${API_BASE}/api/v1/auth/refresh`, (route) =>
      route.fulfill({ status: 401, body: '{}' })
    )

    await page.goto('/projects')

    // Wait for the user to be loaded and the logout button to appear
    const logoutButton = page.getByRole('button', { name: /logout/i })
    await logoutButton.waitFor({ timeout: 10000 })
    await logoutButton.click()

    // Should redirect to /login
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 })
    expect(logoutCalled).toBe(true)
  })

  test('logout clears auth state so protected routes redirect', async ({ page }) => {
    const validJwt = makeFakeJwt(30)

    await page.context().addCookies([
      { name: 'access_token', value: validJwt, domain: 'localhost', path: '/' },
    ])

    await page.route(`${API_BASE}/api/v1/auth/me`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: 'user-1',
          email: 'test@example.com',
          full_name: 'Test User',
          role: 'admin',
          org_id: 'org-1',
        }),
      })
    )

    await page.route(`${API_BASE}/api/v1/auth/logout`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': 'access_token=; Max-Age=0; Path=/',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Logged out' }),
      })
    )

    await page.route(`${API_BASE}/api/v1/auth/refresh`, (route) =>
      route.fulfill({ status: 401, body: '{}' })
    )

    await page.goto('/projects')

    const logoutButton = page.getByRole('button', { name: /logout/i })
    await logoutButton.waitFor({ timeout: 10000 })
    await logoutButton.click()

    await expect(page).toHaveURL(/\/login/, { timeout: 10000 })

    // Now clear cookies (simulate the Set-Cookie clearing) and try to visit /projects again
    await page.context().clearCookies()
    await page.goto('/projects')
    await expect(page).toHaveURL(/\/login/)
  })
})

// ---------------------------------------------------------------------------
// Forgot Password
// ---------------------------------------------------------------------------

test.describe('Forgot Password', () => {
  test('submitting forgot-password shows confirmation', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/forgot-password`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Reset email sent' }),
      })
    )

    await page.goto('/forgot-password')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByRole('button', { name: /send reset link/i }).click()

    await expect(page.getByText(/check your email/i)).toBeVisible({ timeout: 5000 })
  })

  test('forgot-password error shows message', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/forgot-password`, (route) =>
      route.fulfill({
        status: 429,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detail: 'Too many requests' }),
      })
    )

    await page.goto('/forgot-password')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByRole('button', { name: /send reset link/i }).click()

    await expect(page.locator('#forgot-error')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('#forgot-error')).toContainText('Too many requests')
  })

  test('forgot-password has link back to login', async ({ page }) => {
    await page.goto('/forgot-password')
    const backLink = page.getByRole('link', { name: /back to sign in/i })
    await expect(backLink).toBeVisible()
    await expect(backLink).toHaveAttribute('href', '/login')
  })
})

// ---------------------------------------------------------------------------
// Reset Password
// ---------------------------------------------------------------------------

test.describe('Reset Password', () => {
  test('successful password reset shows confirmation', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/reset-password`, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: 'Password reset successful' }),
      })
    )

    await page.goto('/reset-password?token=valid-reset-token')

    await page.getByLabel(/new password/i).fill('NewSecurePass123!')
    await page.getByLabel(/confirm password/i).fill('NewSecurePass123!')
    await page.getByRole('button', { name: /reset password/i }).click()

    await expect(page.getByText(/password reset successful/i)).toBeVisible({ timeout: 5000 })
    // Should show a Sign In link
    await expect(page.getByRole('link', { name: /sign in/i })).toBeVisible()
  })

  test('mismatched passwords shows client-side error', async ({ page }) => {
    await page.goto('/reset-password?token=valid-reset-token')

    await page.getByLabel(/new password/i).fill('NewSecurePass123!')
    await page.getByLabel(/confirm password/i).fill('DifferentPass456!')
    await page.getByRole('button', { name: /reset password/i }).click()

    await expect(page.locator('#reset-error')).toBeVisible()
    await expect(page.locator('#reset-error')).toContainText('Passwords do not match')
  })

  test('password too short shows error', async ({ page }) => {
    await page.goto('/reset-password?token=valid-reset-token')

    await page.getByLabel(/new password/i).fill('Short1!')
    await page.getByLabel(/confirm password/i).fill('Short1!')
    await page.getByRole('button', { name: /reset password/i }).click()

    await expect(page.locator('#reset-error')).toBeVisible()
    await expect(page.locator('#reset-error')).toContainText('at least 12 characters')
  })

  test('password missing uppercase shows error', async ({ page }) => {
    await page.goto('/reset-password?token=valid-reset-token')

    await page.getByLabel(/new password/i).fill('alllowercase123!')
    await page.getByLabel(/confirm password/i).fill('alllowercase123!')
    await page.getByRole('button', { name: /reset password/i }).click()

    await expect(page.locator('#reset-error')).toBeVisible()
    await expect(page.locator('#reset-error')).toContainText('uppercase')
  })

  test('password missing special char shows error', async ({ page }) => {
    await page.goto('/reset-password?token=valid-reset-token')

    await page.getByLabel(/new password/i).fill('NoSpecialChar123')
    await page.getByLabel(/confirm password/i).fill('NoSpecialChar123')
    await page.getByRole('button', { name: /reset password/i }).click()

    await expect(page.locator('#reset-error')).toBeVisible()
    await expect(page.locator('#reset-error')).toContainText('special character')
  })

  test('missing token shows warning', async ({ page }) => {
    await page.goto('/reset-password')
    // Should show a warning about missing token
    await expect(page.getByText(/no reset token/i)).toBeVisible()
    // Submit button should be disabled
    await expect(page.getByRole('button', { name: /reset password/i })).toBeDisabled()
  })
})

// ---------------------------------------------------------------------------
// Login page link to forgot-password
// ---------------------------------------------------------------------------

test.describe('Login Page Navigation', () => {
  test('login page has forgot-password link', async ({ page }) => {
    await page.goto('/login')
    const link = page.getByRole('link', { name: /forgot your password/i })
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', '/forgot-password')
  })

  test('login page renders SSO buttons', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByRole('button', { name: /google/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /microsoft/i })).toBeVisible()
    await expect(page.getByText(/or continue with/i)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Safe Redirect Validation
// ---------------------------------------------------------------------------

test.describe('Safe Redirect Validation', () => {
  test('login respects ?redirect parameter after auth', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login?redirect=/documents')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // Should redirect to /documents, not /projects
    await expect(page).toHaveURL(/\/documents/, { timeout: 10000 })
  })

  test('open redirect with // prefix is blocked', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    // Attempt open redirect via //evil.com
    await page.goto('/login?redirect=//evil.com')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // getSafeRedirect should reject this and default to /projects
    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })
  })

  test('open redirect with protocol is blocked', async ({ page }) => {
    await page.route(`${API_BASE}/api/v1/auth/login`, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          'Set-Cookie': `access_token=${makeFakeJwt()}; HttpOnly; Path=/`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'Login successful' }),
      })
    )

    await page.goto('/login?redirect=https://evil.com')
    await page.getByLabel(/email/i).fill('test@example.com')
    await page.getByLabel(/password/i).fill('TestPassword123!')
    await page.getByRole('button', { name: /sign in/i }).click()

    // getSafeRedirect should reject this and default to /projects
    await expect(page).toHaveURL(/\/projects/, { timeout: 10000 })
  })
})
