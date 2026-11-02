"""Operational helpers; never write to MongoDB or expose credentials."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

if __package__:
    from .source import SyncError
else:
    from source import SyncError

VERSION='1.2.2'


def error_hint(code):
    hints={
        'SOURCE_HTTP_401':'产品源接口未通过认证；检查PRODUCT_API_KEY是否为源接口密钥。',
        'SOURCE_HTTP_403':'产品源接口拒绝访问；核对源接口密钥、接口地址、服务器网络/VPN和IP白名单。不会自动重试403。',
        'SOURCE_HTTP_429':'产品源接口限流；降低请求频率后再试。',
        'SOURCE_NETWORK_UNAVAILABLE':'服务器无法连接产品源接口；检查网络、DNS、代理和防火墙。',
        'SET_PRODUCT_API_KEY_AND_SYNC_MONGODB_URI':'检查product_sync/.env中的源接口密钥及MongoDB同步账号连接字符串。',
        'SET_PRODUCT_API_URL':'在product_sync/.env填写原来成功使用的PRODUCT_API_URL；公开包只提供占位地址。',
        'FINISH_PRODUCT_FIELD_REPAIR_FIRST':'现有产品结构字段类型不符合契约，先检查原库字段，不开始同步写入。',
        'CHANGE_RATIO_REQUIRES_REVIEW_NO_WRITES':'新增/内容变化比例超阈值；先核对变更计划，不要仅为通过而调大阈值。',
        'OperationFailure':'MongoDB认证或权限检查失败；核对同步账号及目标库权限。',
        'ServerSelectionTimeoutError':'无法连接MongoDB；核对地址、端口、服务器网络和认证配置。',
        'REUSE_SNAPSHOT_PREVIEW_ONLY':'历史快照仅用于只读预览，正式写入必须重新拉取全量源数据。',
        'INSUFFICIENT_FREE_DISK_NO_PRODUCT_WRITES':'状态卷空闲空间低于MIN_FREE_DISK_GB；检查磁盘并归档旧运行，尚未开始产品写入。',
        'ANOTHER_SYNC_IS_RUNNING':'同一状态目录已有同步任务运行；不要同时启用另一份调度器。',
    }
    return hints.get(code,'保留本次报告及运行目录，按error代码排查；日志不输出密钥或数据库URI。')


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def reuse_snapshot(state,run_id,target,date_from):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}',run_id):
        raise SyncError('INVALID_SNAPSHOT_RUN_ID')
    source=state/run_id
    try:
        report=json.loads((source/'report.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):raise SyncError('SNAPSHOT_REPORT_UNAVAILABLE') from None
    if report.get('result') not in ('preview_only','success') or report.get('phase') not in ('preview_complete','complete'):
        raise SyncError('SNAPSHOT_RUN_NOT_COMPLETE')
    if report.get('date_from')!=date_from:raise SyncError('SNAPSHOT_DATE_FROM_MISMATCH')
    # Do not recursively inherit an untraceable chain of re-used snapshots.
    if report.get('snapshot_reused_from'):raise SyncError('REUSE_ORIGINAL_SNAPSHOT_RUN_REQUIRED')
    original=source/'stage.sqlite';copy=target/'stage.sqlite'
    if not original.is_file() or original.is_symlink():raise SyncError('SNAPSHOT_FILE_UNAVAILABLE')
    before=file_hash(original)
    if report.get('snapshot_sha256') and report['snapshot_sha256']!=before:
        raise SyncError('SNAPSHOT_HASH_MISMATCH')
    shutil.copyfile(original,copy)
    if before!=file_hash(copy) or before!=file_hash(original):raise SyncError('SNAPSHOT_CHANGED_DURING_COPY')
    db=sqlite3.connect(copy.as_uri()+'?mode=ro',uri=True)
    try:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise SyncError('SNAPSHOT_SQLITE_INVALID')
        stats=report['snapshot']
        products=db.execute('SELECT count(*) FROM type1').fetchone()[0]
        attributes=db.execute('SELECT count(*) FROM type2').fetchone()[0]
        # Raw page row counts can exceed staged counts when versions were resolved.
        if products!=stats['unique_products'] or attributes>stats['type2']['rows']:
            raise SyncError('SNAPSHOT_COUNTS_MISMATCH')
        if report.get('staged_type2_rows') is not None and attributes!=report['staged_type2_rows']:
            raise SyncError('SNAPSHOT_COUNTS_MISMATCH')
        return db,stats,{'snapshot_reused_from':run_id,'snapshot_fetched_at':report.get('snapshot_fetched_at') or report.get('finished_at'),
                        'snapshot_sha256':before,'staged_type2_rows':attributes,
                        'snapshot_integrity':'sha256_verified' if report.get('snapshot_sha256') else 'legacy_snapshot_counts_checked'}
    except Exception:
        db.close();raise


def report_summary(report):
    """Small public status summary; do not echo arbitrary imported report fields."""
    allowed=('run_id','mode','phase','result','service_version','started_at','finished_at',
             'updated_at','elapsed_seconds','counts','impact','fetch_progress','error','error_hint','failed_phase',
             'products_write_started','applied_operations','verified_operations','snapshot_reused_from')
    return {key:report[key] for key in allowed if key in report}

