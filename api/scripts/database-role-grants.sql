/*
 * 由 Migration Role 在建表后执行的最小权限模板。
 * 调用方通过 psql -v runtime_role=... -v bootstrap_role=... -v audit_reader_role=... 注入角色名。
 */
GRANT USAGE ON SCHEMA public TO :"runtime_role", :"bootstrap_role", :"audit_reader_role";

GRANT SELECT, INSERT, UPDATE ON users, auth_sessions, refresh_tokens
  TO :"runtime_role";
GRANT INSERT ON security_audit_events
  TO :"runtime_role";

GRANT SELECT, INSERT ON users
  TO :"bootstrap_role";
GRANT INSERT ON security_audit_events
  TO :"bootstrap_role";

GRANT SELECT ON security_audit_events
  TO :"audit_reader_role";

REVOKE UPDATE, DELETE ON security_audit_events
  FROM :"runtime_role", :"bootstrap_role", :"audit_reader_role";
REVOKE SELECT ON security_audit_events
  FROM :"runtime_role", :"bootstrap_role";
