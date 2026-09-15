-- 尽调报告检查历史：一行一次检查，结果文件在 COS。代码不执行本文件。
-- dd_check check history. Application code does not execute this file.
-- Result body lives in COS; this table stores pointers only.

CREATE TABLE dd_check_check_run (
  check_id VARCHAR(36) NOT NULL COMMENT '本次检查 id，uuid',
  report_id VARCHAR(64) NOT NULL COMMENT '业务尽调报告 id，可重复',
  scenario VARCHAR(64) NOT NULL COMMENT '场景 id，如 default / corp_onboarding / corp_change',
  scenario_title VARCHAR(128) NOT NULL DEFAULT '' COMMENT '场景展示名',
  filename VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Word 文件名',
  content_type VARCHAR(128) NOT NULL DEFAULT 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' COMMENT '文件 Content-Type',
  object_key VARCHAR(512) NOT NULL DEFAULT '' COMMENT '本包 COS 对象键',
  created_at DATETIME(3) NOT NULL COMMENT '检查时间',
  updated_at DATETIME(3) NOT NULL COMMENT '更新时间，插入时与 created_at 相同',
  PRIMARY KEY (check_id),
  KEY idx_report_created (report_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='尽调报告检查历史，结果以 COS 文件为准';
