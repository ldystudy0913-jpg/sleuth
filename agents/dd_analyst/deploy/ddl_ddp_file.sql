-- =============================================================================
-- dd_analyst 附件元数据表（MySQL）
-- =============================================================================
-- 用途：按 invest_id 查出 COS/对象存储上的文件路径，供附件检查节点下载解密。
-- 代码行为：只 SELECT，不会 CREATE / INSERT / UPDATE / DELETE。
--
-- 默认约定（与 agents/dd_analyst/.env.example 一致）：
--   DD_CHECK_MYSQL_DDP_FILE_TABLE=ddp_file
--   DD_CHECK_MYSQL_INVEST_ID_COLUMN=invest_id
--   DD_CHECK_MYSQL_LOCATION_PATH_COLUMN=location_path
--
-- 若你们库里已有同名业务表且列名不同，不要硬改表；改环境变量列名即可。
-- =============================================================================

CREATE TABLE IF NOT EXISTS `ddp_file` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `invest_id`     VARCHAR(64)     NOT NULL COMMENT '调查单号 / 尽调业务主键（对应请求 investId）',
  `location_path` VARCHAR(1024)   NOT NULL COMMENT '对象存储路径或 key（COS location）',
  `file_name`     VARCHAR(512)    NULL COMMENT '原始文件名（可选，代码当前不读）',
  `mime`          VARCHAR(128)    NULL COMMENT 'MIME（可选，代码当前不读）',
  `file_size`     BIGINT UNSIGNED NULL COMMENT '字节大小（可选，代码当前不读）',
  `created_at`    DATETIME        NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME        NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_ddp_file_invest_id` (`invest_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='尽调附件元数据（供 dd_check 只读）';

-- 代码实际执行的查询等价于：
-- SELECT location_path AS location_path
-- FROM ddp_file
-- WHERE invest_id = %s;

-- 示例数据（把 location_path 换成你们 COS 上真实 object key）：
-- INSERT INTO ddp_file (invest_id, location_path, file_name)
-- VALUES ('INV-001', 'dd/attachments/INV-001/idcard.bin', 'idcard.bin');
