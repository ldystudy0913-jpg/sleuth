-- =============================================================================
-- 反洗钱身份目录 + Agent/Skill 授权（与会话同一 MySQL / 本地 SQLite）
-- =============================================================================
-- 代码只 SELECT/INSERT/UPDATE，不执行本文件。缺表则 ACL 降级为「全可见」。
-- 表名须与 SLEUTH_ACL_TABLE_ORG/ROLE/USER/GRANT 一致（默认即下列表名）。
--
-- 下面每张表后的 INSERT 是同一套测试数据，取消注释即可：
--   机构 SZ_BR（深圳分行）
--   岗位 aml_analyst（可疑分析岗）
--   用户 emp_zhang（请求头 X-User-Id: emp_zhang）
--   该岗允许 agent=dd_reply、skill=dd-reply-framework
-- 测默认助手：SLEUTH_ACL_DEFAULT_AGENT_OPEN=1 时不必给 build 再插 grant。
-- =============================================================================

CREATE TABLE mem_org (
  org_id VARCHAR(64) NOT NULL COMMENT '机构编码，主键，与行内组织编码对齐',
  parent_id VARCHAR(64) DEFAULT NULL COMMENT '上级机构编码，总行为空',
  org_category VARCHAR(16) NOT NULL COMMENT '机构类别：head总行/branch分行/sub支行',
  org_name VARCHAR(128) NOT NULL COMMENT '机构显示名称，不写入模型',
  row_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '行状态：active可用/disabled停用',
  created_at DATETIME NOT NULL COMMENT '创建时间',
  updated_at DATETIME NOT NULL COMMENT '最后修改时间',
  PRIMARY KEY (org_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='反洗钱机构目录';

-- INSERT INTO mem_org
--   (org_id, parent_id, org_category, org_name, row_status, created_at, updated_at)
-- VALUES
--   ('SZ_BR', NULL, 'branch', '深圳分行', 'active', NOW(), NOW());

CREATE TABLE mem_role (
  role_id VARCHAR(64) NOT NULL COMMENT '岗位编码，主键',
  role_name VARCHAR(128) NOT NULL COMMENT '岗位显示名称',
  scenario_list VARCHAR(512) DEFAULT NULL COMMENT '岗位相关场景编码，逗号分隔，如 due_diligence,screening',
  row_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '行状态：active可用/disabled停用',
  created_at DATETIME NOT NULL COMMENT '创建时间',
  updated_at DATETIME NOT NULL COMMENT '最后修改时间',
  PRIMARY KEY (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='反洗钱岗位目录';

-- INSERT INTO mem_role
--   (role_id, role_name, scenario_list, row_status, created_at, updated_at)
-- VALUES
--   ('aml_analyst', '可疑分析岗', 'suspicious_analysis,due_diligence,screening', 'active', NOW(), NOW());

CREATE TABLE mem_user (
  user_id VARCHAR(64) NOT NULL COMMENT '用户编码，主键，与请求头 X-User-Id 一致',
  display_name VARCHAR(128) DEFAULT NULL COMMENT '展示名，仅管理端使用，不写入模型',
  role_id VARCHAR(64) DEFAULT NULL COMMENT '唯一岗位编码，对应 mem_role.role_id',
  org_id VARCHAR(64) DEFAULT NULL COMMENT '唯一机构编码，对应 mem_org.org_id',
  row_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '行状态：active在职/disabled停用',
  created_at DATETIME NOT NULL COMMENT '创建时间',
  updated_at DATETIME NOT NULL COMMENT '最后修改时间',
  PRIMARY KEY (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户目录，一人一岗一机构';

-- INSERT INTO mem_user
--   (user_id, display_name, role_id, org_id, row_status, created_at, updated_at)
-- VALUES
--   ('emp_zhang', '张三', 'aml_analyst', 'SZ_BR', 'active', NOW(), NOW());
-- -- 换岗测试：把 role_id 改成另一个岗位编码即可，不必改 grant、也不必改历史 user 记忆。

CREATE TABLE mem_grant (
  grant_id VARCHAR(64) NOT NULL COMMENT '授权主键',
  scope_kind VARCHAR(16) NOT NULL COMMENT '授权主体类别：role岗位/org机构/user用户例外',
  scope_id VARCHAR(64) NOT NULL COMMENT '主体编码，对应 role_id 或 org_id 或 user_id',
  resource_kind VARCHAR(16) NOT NULL COMMENT '资源类别：agent 或 skill',
  resource_id VARCHAR(128) NOT NULL COMMENT '资源名，如 build、dd_reply、dd-reply-framework',
  grant_effect VARCHAR(16) NOT NULL COMMENT '效力：allow允许/deny拒绝（deny仅用于用户例外）',
  row_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '行状态：active生效/disabled停用',
  created_at DATETIME NOT NULL COMMENT '创建时间',
  updated_at DATETIME NOT NULL COMMENT '最后修改时间',
  PRIMARY KEY (grant_id),
  UNIQUE KEY uk_mem_grant (scope_kind, scope_id, resource_kind, resource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent与Skill授权，以岗位为主';

-- INSERT INTO mem_grant
--   (grant_id, scope_kind, scope_id, resource_kind, resource_id, grant_effect, row_status, created_at, updated_at)
-- VALUES
--   ('grant_demo_role_agent', 'role', 'aml_analyst', 'agent', 'dd_reply', 'allow', 'active', NOW(), NOW()),
--   ('grant_demo_role_skill', 'role', 'aml_analyst', 'skill', 'dd-reply-framework', 'allow', 'active', NOW(), NOW());
-- -- 用户例外（可选）：临时禁止张三使用尽调 agent，岗位其他人不受影响
-- -- INSERT INTO mem_grant
-- --   (grant_id, scope_kind, scope_id, resource_kind, resource_id, grant_effect, row_status, created_at, updated_at)
-- -- VALUES
-- --   ('grant_demo_user_deny', 'user', 'emp_zhang', 'agent', 'dd_reply', 'deny', 'active', NOW(), NOW());
