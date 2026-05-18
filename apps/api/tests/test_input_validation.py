from __future__ import annotations

from app.services.security.input_validator import InputValidator


class TestInputValidation:
    def test_clean_text_passes(self):
        v = InputValidator()
        is_safe, _sanitized = v.validate_text_input("Normal project name")
        assert is_safe is True

    def test_sql_injection_project_name(self):
        # M-10: SQL keyword patterns are logged but no longer blocked (the app
        # uses parameterized queries everywhere, and blocking these rejected
        # legitimate construction input like `O'Brien Construction`).
        v = InputValidator()
        is_safe, _ = v.validate_text_input("'; DROP TABLE projects; --")
        assert is_safe is True

    def test_sql_injection_or_1_equals_1(self):
        v = InputValidator()
        is_safe, _ = v.validate_text_input("' OR 1=1 --")
        assert is_safe is True

    def test_sql_injection_union_select(self):
        v = InputValidator()
        is_safe, _ = v.validate_text_input("' UNION SELECT * FROM users --")
        assert is_safe is True

    def test_xss_script_tag(self):
        v = InputValidator()
        sanitized = v.sanitize_html("<script>alert('xss')</script>Hello")
        assert "<script>" not in sanitized
        assert "Hello" in sanitized

    def test_xss_event_handler(self):
        v = InputValidator()
        is_safe, _ = v.validate_text_input('<img onerror="alert(1)">')
        assert is_safe is False

    def test_search_query_clean(self):
        v = InputValidator()
        is_safe, _sanitized = v.validate_search_query("concrete specifications")
        assert is_safe is True

    def test_search_query_injection(self):
        v = InputValidator()
        is_safe, _ = v.validate_search_query("' OR 1=1 --")
        assert is_safe is False
