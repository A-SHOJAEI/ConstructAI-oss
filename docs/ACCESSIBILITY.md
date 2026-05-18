# Accessibility Guide

## Standards

ConstructAI aims for **WCAG 2.1 Level AA** compliance across all user-facing pages.

## Implemented Features

### Keyboard Navigation
- All interactive elements are reachable via Tab key
- Focus indicators are visible on all focusable elements
- Global keyboard shortcuts available (press Shift+? for list)
- Escape key closes dialogs and panels

### Screen Reader Support
- Semantic HTML elements used throughout (nav, main, header, etc.)
- ARIA labels on interactive elements without visible text
- ARIA roles on custom components (role="dialog", role="switch", etc.)
- Live regions for dynamic content updates (alerts, notifications)

### Color and Contrast
- Text contrast ratios meet WCAG AA requirements (4.5:1 for normal text, 3:1 for large text)
- Color is never the sole indicator of state — icons and text accompany color changes
- Dark mode support with appropriate contrast ratios

### Forms
- All form inputs have associated labels (htmlFor/id pairs)
- Error messages are linked to inputs via aria-describedby
- Required fields are marked with visual and programmatic indicators
- Form validation messages are announced to screen readers

### Images and Media
- Decorative images use empty alt text (alt="")
- Informational images have descriptive alt text
- Charts include text alternatives for data

### Motion and Animation
- Animations respect `prefers-reduced-motion` media query
- No content flashes more than 3 times per second
- Loading spinners include aria-label descriptions

## Testing

### Automated Testing
- axe-core integration via Playwright for automated accessibility audits
- CI pipeline includes accessibility checks on key pages

### Manual Testing
- Test with keyboard-only navigation
- Test with screen readers (NVDA, VoiceOver)
- Test with high contrast mode
- Test with 200% zoom

## Known Limitations

- Some third-party chart libraries may have limited screen reader support
- PDF documents may not be fully accessible depending on source formatting
- Camera feed previews don't have audio descriptions

## Reporting Issues

If you encounter accessibility barriers, please report them:
- Email: support@constructai.dev
- Include the page URL, browser, and assistive technology used
- Describe the barrier and expected behavior
