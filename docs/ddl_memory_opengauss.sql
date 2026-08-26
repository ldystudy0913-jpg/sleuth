-- =============================================================================
-- 分层长期记忆（OpenGauss 库 sleuth_memory，Postgres 协议）
-- =============================================================================
-- 代码只读写行，不执行本文件。缺表则记忆关闭，对话与 ACL 不受影响。
-- 表放在业务 schema 时（例如 aml_gs），请先执行：SET search_path TO aml_gs;
-- 或把表名写成 aml_gs.mem_item。Sleuth 侧配 SLEUTH_OG_SCHEMA=aml_gs（勿依赖 public / information_schema）。
-- 表名须与 SLEUTH_MEMORY_TABLE_ITEM / SLEUTH_MEMORY_TABLE_AUDIT 一致。
-- embedding 维度须与 SLEUTH_EMBEDDING_DIM、建表 vector(n) 三者一致（下例 1024）。
-- 无 vector 插件时把 embedding 改成 real[]，去掉 HNSW，并设 SLEUTH_MEMORY_VECTOR_KIND=real_array。
-- 实例若用 FLOATVECTOR(n) 而非 pgvector 的 vector(n)，设 SLEUTH_MEMORY_VECTOR_KIND=floatvector。
-- 实例若不能建 TEXT、把 body_text/payload_text 改成 JSONB，设 SLEUTH_MEMORY_TEXT_KIND=jsonb。
-- 无权限建 HNSW 时可省略向量索引；数据量小时顺序扫描仍可召回，不影响读写逻辑。
-- embedding NOT NULL 时示例插入必须带向量，不能写 NULL。
--
-- 插入示例与 docs/ddl_memory_mysql.sql 同一套人：emp_zhang / aml_analyst / SZ_BR。
-- 手工插入时 embedding 可先 NULL（列表/管理端可见）；向量召回需由 Sleuth 写入时计算。
-- =============================================================================

CREATE TABLE mem_item (
  id VARCHAR(64) NOT NULL,
  scope_kind VARCHAR(16) NOT NULL,
  scope_id VARCHAR(64) NOT NULL,
  scenario_code VARCHAR(32) NOT NULL,
  mem_kind VARCHAR(32) NOT NULL,
  item_key VARCHAR(128) NOT NULL,
  title_text VARCHAR(128) NOT NULL,
  body_text TEXT NOT NULL,
  payload_text TEXT,
  embedding vector(1024),
  importance_score INTEGER NOT NULL,
  confidence_score DECIMAL(5,4) NOT NULL,
  origin_type VARCHAR(32) NOT NULL,
  row_status VARCHAR(16) NOT NULL,
  expire_at TIMESTAMP,
  created_by VARCHAR(64) NOT NULL,
  updated_by VARCHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  last_used_at TIMESTAMP,
  use_cnt INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  CONSTRAINT uk_mem_item UNIQUE (scope_kind, scope_id, scenario_code, mem_kind, item_key)
);

COMMENT ON TABLE mem_item IS '分层长期记忆，仅存已脱敏文本与向量';
COMMENT ON COLUMN mem_item.id IS '记忆主键，API 路径使用';
COMMENT ON COLUMN mem_item.scope_kind IS '归属层级：user/role/org';
COMMENT ON COLUMN mem_item.scope_id IS '归属对象编码，对应用户或岗位或机构';
COMMENT ON COLUMN mem_item.scenario_code IS '场景编码：general/suspicious_analysis/due_diligence/screening/rating';
COMMENT ON COLUMN mem_item.mem_kind IS '记忆类型：preference/workflow/policy/fact/pattern/forget';
COMMENT ON COLUMN mem_item.item_key IS '稳定业务键，同归属下原地更新，不用保留字 key';
COMMENT ON COLUMN mem_item.title_text IS '短标题';
COMMENT ON COLUMN mem_item.body_text IS '已脱敏正文，注入模型并参与 embedding';
COMMENT ON COLUMN mem_item.payload_text IS '前端结构化内容，文本存放 JSON 字符串';
COMMENT ON COLUMN mem_item.embedding IS 'title与正文的向量，维度须与嵌入模型一致';
COMMENT ON COLUMN mem_item.importance_score IS '重要度1到5，整数';
COMMENT ON COLUMN mem_item.confidence_score IS '确信度0到1，十进制';
COMMENT ON COLUMN mem_item.origin_type IS '来源：user_explicit/agent_inferred/admin，不用保留字 source';
COMMENT ON COLUMN mem_item.row_status IS '行状态：active可检索/archived已忘记';
COMMENT ON COLUMN mem_item.expire_at IS '过期时间，空表示不过期';
COMMENT ON COLUMN mem_item.created_by IS '首次写入者用户编码';
COMMENT ON COLUMN mem_item.updated_by IS '最后修改者用户编码';
COMMENT ON COLUMN mem_item.created_at IS '创建时间';
COMMENT ON COLUMN mem_item.updated_at IS '最后修改时间';
COMMENT ON COLUMN mem_item.last_used_at IS '最近一次被召回注入的时间';
COMMENT ON COLUMN mem_item.use_cnt IS '被召回次数';

CREATE INDEX idx_mem_item_embedding ON mem_item USING hnsw (embedding vector_cosine_ops);

-- -- 个人偏好（保底注入，即使向量分一般也会进 prompt）
-- INSERT INTO mem_item (
--   id, scope_kind, scope_id, scenario_code, mem_kind, item_key,
--   title_text, body_text, payload_text, embedding,
--   importance_score, confidence_score, origin_type, row_status, expire_at,
--   created_by, updated_by, created_at, updated_at, last_used_at, use_cnt
-- ) VALUES (
--   'mem_demo_user_lang', 'user', 'emp_zhang', 'general', 'preference', 'output.language',
--   '回复语言', '默认用中文回复，条理清晰，少用英文缩写。', NULL, NULL,
--   4, 1.0000, 'user_explicit', 'active', NULL,
--   'emp_zhang', 'emp_zhang', NOW(), NOW(), NULL, 0
-- );
--
-- -- 岗位口径（该岗全员共享；同 item_key 时会被 user 层覆盖）
-- INSERT INTO mem_item (
--   id, scope_kind, scope_id, scenario_code, mem_kind, item_key,
--   title_text, body_text, payload_text, embedding,
--   importance_score, confidence_score, origin_type, row_status, expire_at,
--   created_by, updated_by, created_at, updated_at, last_used_at, use_cnt
-- ) VALUES (
--   'mem_demo_role_str', 'role', 'aml_analyst', 'suspicious_analysis', 'policy', 'str.threshold',
--   '可疑报告口径', '先写资金链路与交易对手，再写可疑点；不要把完整证件号写进结论。', NULL, NULL,
--   5, 0.9000, 'admin', 'active', NULL,
--   'admin', 'admin', NOW(), NOW(), NULL, 0
-- );
--
-- -- 机构制度摘要（分行全员；同 item_key 时 user > role > org）
-- INSERT INTO mem_item (
--   id, scope_kind, scope_id, scenario_code, mem_kind, item_key,
--   title_text, body_text, payload_text, embedding,
--   importance_score, confidence_score, origin_type, row_status, expire_at,
--   created_by, updated_by, created_at, updated_at, last_used_at, use_cnt
-- ) VALUES (
--   'mem_demo_org_str', 'org', 'SZ_BR', 'suspicious_analysis', 'policy', 'str.threshold',
--   '分行可疑报告口径', '深圳分行补充：现金密集与夜间交易要单独成段。', NULL, NULL,
--   3, 0.8000, 'admin', 'active', NULL,
--   'admin', 'admin', NOW(), NOW(), NULL, 0
-- );
--
-- -- 负向约束（forget，user 级也会保底注入）
-- INSERT INTO mem_item (
--   id, scope_kind, scope_id, scenario_code, mem_kind, item_key,
--   title_text, body_text, payload_text, embedding,
--   importance_score, confidence_score, origin_type, row_status, expire_at,
--   created_by, updated_by, created_at, updated_at, last_used_at, use_cnt
-- ) VALUES (
--   'mem_demo_user_forget', 'user', 'emp_zhang', 'general', 'forget', 'avoid.verbose_english',
--   '不要用英文长段', '用户已说明不要大段英文解释，结论用中文。', NULL, NULL,
--   3, 1.0000, 'user_explicit', 'active', NULL,
--   'emp_zhang', 'emp_zhang', NOW(), NOW(), NULL, 0
-- );
--
-- -- pattern 类建议带过期（配置 SLEUTH_MEMORY_PATTERN_TTL_DAYS，默认约 90 天）
-- INSERT INTO mem_item (
--   id, scope_kind, scope_id, scenario_code, mem_kind, item_key,
--   title_text, body_text, payload_text, embedding,
--   importance_score, confidence_score, origin_type, row_status, expire_at,
--   created_by, updated_by, created_at, updated_at, last_used_at, use_cnt
-- ) VALUES (
--   'mem_demo_user_pattern', 'user', 'emp_zhang', 'suspicious_analysis', 'pattern', 'pattern.cash_night',
--   '夜间现金密集', '分析套路：先看夜间现金占比，再看对手是否分散，禁止写客户姓名或账号。', NULL, NULL,
--   3, 0.7000, 'agent_inferred', 'active', NOW() + INTERVAL '90 days',
--   'emp_zhang', 'emp_zhang', NOW(), NOW(), NULL, 0
-- );

CREATE TABLE mem_audit (
  audit_id VARCHAR(64) NOT NULL,
  memory_id VARCHAR(64) NOT NULL,
  action_type VARCHAR(16) NOT NULL,
  actor_user_id VARCHAR(64) NOT NULL,
  acted_at TIMESTAMP NOT NULL,
  detail_text VARCHAR(512),
  PRIMARY KEY (audit_id)
);

COMMENT ON TABLE mem_audit IS '记忆审计，不给模型看，不存正文与向量';
COMMENT ON COLUMN mem_audit.audit_id IS '审计主键';
COMMENT ON COLUMN mem_audit.memory_id IS '对应 mem_item.id';
COMMENT ON COLUMN mem_audit.action_type IS '操作类型：create/update/archive/forget/retrieve';
COMMENT ON COLUMN mem_audit.actor_user_id IS '操作人用户编码';
COMMENT ON COLUMN mem_audit.acted_at IS '操作时间';
COMMENT ON COLUMN mem_audit.detail_text IS '短说明，如 item_key=output.language';

CREATE INDEX idx_mem_audit_memory ON mem_audit (memory_id);

-- INSERT INTO mem_audit
--   (audit_id, memory_id, action_type, actor_user_id, acted_at, detail_text)
-- VALUES
--   ('audit_demo_create_lang', 'mem_demo_user_lang', 'create', 'emp_zhang', NOW(), 'item_key=output.language');
