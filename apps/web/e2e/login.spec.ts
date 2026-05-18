import { test, expect } from '@playwright/test'

test.describe('Login Page', () => {
  test('should display login form', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByLabel(/email/i)).toBeVisible()
    await expect(page.getByLabel(/password/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /sign in|log in/i })).toBeVisible()
  })

  test('should show error on invalid credentials', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel(/email/i).fill('invalid@example.com')
    await page.getByLabel(/password/i).fill('wrongpassword')
    await page.getByRole('button', { name: /sign in|log in/i }).click()
    // Should show error message or stay on login page
    await expect(page).toHaveURL(/login/)
  })

  test('should redirect to projects on successful login', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel(/email/i).fill('admin@constructai.com')
    await page.getByLabel(/password/i).fill('admin123')
    await page.getByRole('button', { name: /sign in|log in/i }).click()
    // Should redirect after login
    await page.waitForURL(/projects|dashboard/, { timeout: 10000 })
    await expect(page.url()).toContain('/projects')
  })

  test('should redirect unauthenticated users to login', async ({ page }) => {
    await page.goto('/projects')
    await expect(page).toHaveURL(/login/)
  })
})
