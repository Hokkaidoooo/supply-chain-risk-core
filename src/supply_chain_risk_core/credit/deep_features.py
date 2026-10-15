"""工商字段特征工程；缺失使用显式哨兵，入口校验由 credit.workflow 负责。"""
import re
import unicodedata

import numpy as np
import pandas as pd

from ..missing_values import is_missing_text

# ---------------------------------------------------------------- 常量
_FX = {'元': 1.0, '人民币': 1.0, '美元': 7.2, '欧元': 7.8, '香港元': 0.92,
       '港元': 0.92, '英镑': 9.1, '日元': 0.05}          # 折万元人民币(近似汇率, 仅作可比性)

_TIER1 = {'北京', '上海', '广州', '深圳'}
_NEW_TIER1 = {'成都', '重庆', '杭州', '西安', '武汉', '苏州', '郑州', '南京',
              '天津', '长沙', '东莞', '宁波', '佛山', '合肥', '青岛'}
_TIER2 = {'无锡', '济南', '厦门', '福州', '温州', '金华', '常州', '泉州', '南通',
          '徐州', '潍坊', '烟台', '绍兴', '台州', '嘉兴', '惠州', '中山', '珠海',
          '洛阳', '绵阳', '扬州', '盐城', '泰州', '镇江', '临沂', '淄博', '威海'}

_INDUSTRIAL_KW = r'工业园|产业园|科技园|工业区|开发区|高新区|经济区|厂房|工业城|科技城'
_OFFICE_KW = r'大厦|写字楼|广场|中心\d|室$'
_NAME_TECH_KW = r'科技|智能|新材料|新能源|生物|环保|半导体|光电|数字|信息技术'
_NAME_TRADE_KW = r'贸易|商贸|经贸|电子商务|供应链|进出口'
_NAME_GROUP_KW = r'集团|控股'

_SCOPE_IMPEX_KW = r'货物进出口|技术进出口|进出口业|进出口贸易|自营和代理各类商品|进出口'
_SCOPE_HAZCHEM_KW = r'危险化学品|易制毒|监控化学品'
_SCOPE_METAL_KW = r'金属材料|钢材|有色金属|金属制品销售'
_SCOPE_LABOR_KW = r'劳务派遣'
_SCOPE_FINLIKE_KW = r'融资担保|融资租赁|典当|商业保理|小额贷款|房地产开发'
# 高风险关键词池(计数用): 类金融/房开/垫资/税险高发/强监管
_SCOPE_RISK_POOL = ['融资担保', '融资租赁', '典当', '商业保理', '小额贷款', '房地产开发',
                    '危险化学品', '成品油', '劳务派遣', '再生资源回收', '建筑工程施工',
                    '烟花爆竹', '民用爆炸']

DEEP_FEATURE_COLS = [
    # 资本
    'reg_capital_wan', 'paid_capital_wan', 'paid_in_ratio', 'paid_in_disclosed',
    'paid_in_full', 'log1p_reg_capital', 'cap_foreign_currency',
    # 日期
    'company_age_years', 'approval_days_ago', 'approval_within_180d',
    'approval_within_1y', 'approval_stale_3y', 'approval_gap_days', 'never_changed',
    'firm_event_date_after_asof',
    # 营业期限
    'biz_term_disclosed', 'biz_term_expired', 'biz_term_expiring_2y', 'biz_term_remain_years',
    # 企业类型治理
    'ent_type_tier', 'is_sole_natural_person', 'is_corp_sole', 'is_foreign',
    'is_share_co', 'is_state_owned',
    # 变更/重组
    'has_former_name', 'addr_mismatch', 'addr_mismatch_strong', 'annual_addr_missing',
    'rename_recent_change',
    # 场所/数字化
    'has_website', 'addr_industrial', 'addr_office_only',
    # 经营范围
    'scope_len', 'scope_n_items', 'scope_new_format', 'scope_licensed',
    'scope_import_export', 'scope_hazard_chem', 'scope_metal_commodity',
    'scope_labor_dispatch', 'scope_finance_like', 'scope_risk_kw_count',
    # 用工/空壳
    'insured_n', 'insured_missing', 'insured_zero', 'shell_flag', 'shell_score',
    'labor_intensity',
    # 区域
    'city_tier', 'city_firm_cnt', 'district_firm_cnt',
    # 法人关联
    'legalrep_firm_cnt_city', 'legalrep_multi_firm_same_city',
    'legalrep_distinct_city_cnt', 'legalrep_multi_city',
    # 名称
    'name_tech', 'name_trade', 'name_group', 'name_len',
]

# 各特征缺失填充值(保证输出无NaN; -1=未知, 与真实0区分)
_FILL = {c: 0 for c in DEEP_FEATURE_COLS}
_FILL.update({'insured_n': -1, 'labor_intensity': -1, 'approval_days_ago': -1,
              'approval_gap_days': -1, 'company_age_years': -1,
              'biz_term_remain_years': 99, 'paid_in_ratio': -1,
              'reg_capital_wan': -1, 'paid_capital_wan': -1, 'log1p_reg_capital': -1,
              'city_tier': 4, 'ent_type_tier': 4,
              'scope_len': 0, 'scope_n_items': 0, 'name_len': 0,
              'city_firm_cnt': 1, 'district_firm_cnt': 1,
              'legalrep_firm_cnt_city': 1, 'legalrep_distinct_city_cnt': 1})


# ---------------------------------------------------------------- 工具
def _col(f: pd.DataFrame, name: str) -> pd.Series:
    """取列并清洗为字符串; 列不存在则返回'-'占位, 保证不崩溃。

    缺失一律归一为 '-'，词表取自 missing_values（与 data_loading / field_profile
    同一真源）。此前只认 nan/None/NaT/空串，导致 网址='无'→has_website=1、
    最新年报地址='未公示'→addr_mismatch_strong=1 这类"存在类"特征静默反向。
    """
    if name in f.columns:
        s = f[name]
    else:
        s = pd.Series('-', index=f.index)
    s = s.astype(str)
    s = s.str.replace('_x000D_', '', regex=False).str.replace('\r', '', regex=False)
    s = s.str.replace('\n', '', regex=False).str.strip()
    return s.map(lambda v: '-' if is_missing_text(v) else v)


def _parse_capital(s: pd.Series) -> pd.Series:
    """将元/万/亿元及常见币种统一为万元人民币；无法解析保留 NaN。

    先做 NFKC 归一并去空格类分隔：全角逗号/全角数字/空格/NBSP 千分位
    （'1，000万元'、'1 000万元'、'１０００万元'）此前静默解析成 1.0 万，
    千倍缩水且混合命中时无闸门（2026-07-26 review）。
    """
    s = s.map(lambda v: unicodedata.normalize('NFKC', str(v)))
    s = s.str.replace(r'\s', '', regex=True)
    s = s.str.replace('\u2212', '-', regex=False)
    cleaned = s.str.replace(',', '', regex=False)
    number = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    num = pd.to_numeric(
        cleaned.str.extract('(' + number + ')', expand=False),
        errors='coerce')
    num = num.where((num >= 0) & np.isfinite(num) & (cleaned.str.count(number) == 1)
                    & ~cleaned.str.contains(r'\([+-]?\d', regex=True))
    unit = pd.Series(1.0, index=s.index)
    unit = unit.mask(s.str.contains('亿', regex=False), 10000.0)
    unit = unit.mask(~s.str.contains('亿', regex=False) & s.str.contains('万', regex=False), 1.0)
    # 没有万/亿但明确以元计价（含美元/港元等）时，从元换算为万元。
    unit = unit.mask(~s.str.contains('亿|万', regex=True) & s.str.contains('元', regex=False), 0.0001)

    currency = pd.Series(1.0, index=s.index)
    # 长名称优先，避免“香港元”先命中“元”。
    for name in sorted(_FX, key=len, reverse=True):
        if name in ('元', '人民币'):
            continue
        currency = currency.mask(s.str.contains(name, regex=False), _FX[name])
    result = num * unit * currency
    return result.where(np.isfinite(result))


_CN_DATE_RE = re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?')


def _parse_dates(s: pd.Series) -> pd.Series:
    """逐项解析混合日期，避免 pandas 2.x 按首项格式推断导致整批静默 NaT。

    额外接受 'YYYY年M月D日' 中文写法（工商导出常见）；pd.Timestamp 对它抛
    DateParseError，若不先归一会让 company_age_years 整列塌成 -1 哨兵，
    放宽白名单年龄闸门恒失败、空壳检测同时失效。
    """
    def one(value):
        text = str(value).strip()
        if text in ('', '-', 'nan', 'None', 'NaT'):
            return pd.NaT
        m = _CN_DATE_RE.fullmatch(text)
        if m:
            value = '%s-%s-%s' % m.groups()
        try:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp):
                return pd.NaT
            if stamp.tzinfo is not None:
                stamp = stamp.tz_localize(None)
            return stamp.normalize()
        except Exception:
            return pd.NaT
    return s.map(one).astype('datetime64[ns]')


_TERM_NO_END_RE = re.compile(r'长期|无固定|无期限|永久')
_TERM_DATE_RE = re.compile(r'\d{4}\s*[年/.\-]\s*\d{1,2}\s*[月/.\-]\s*\d{1,2}\s*日?')


def _term_end_text(value: str):
    """从营业期限文本提取终止日文本；无终点语义时返回 None。

    不依赖字面'至'：两个日期取后者（分隔符任意，兼容 -/~/到/to）；单个日期
    仅当带明确终止标记、或整个单元格就是一个日期（Excel 日期单元格经 str 化，
    含尾部 00:00:00）时才视为终点——'2010-03-10起'（只有起始日）与
    '2010-03-10至长期'（无固定期限）都不得误判为已有终点。
    """
    text = str(value).strip()
    if text == '-' or _TERM_NO_END_RE.search(text):
        return None
    text = re.sub(r'\s*\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?$', '', text)
    tokens = _TERM_DATE_RE.findall(text)
    if not tokens:
        return None
    if len(tokens) >= 2:
        return tokens[-1]
    tok = tokens[0]
    if re.search(r'(?:至|到|止|to|~|～|—|–)\s*' + re.escape(tok), text):
        return tok
    if text.replace(tok, '', 1).strip() == '':
        return tok
    return None


def _term_bare_single_date(value: str):
    """识别"整格只有一个日期且无任何终止标记"的营业期限写法，返回该日期文本。

    这种形态语义两可：既可能是 Excel 日期单元格存的真终点，也可能是长期户
    省略"至长期"只写起始日。与成立日期相同时应按起始日处理（调用方比对），
    否则会伪造 biz_term_expired → R2 组合收紧/封锁放宽（2026-07-26 review）。
    """
    text = str(value).strip()
    if text == '-' or _TERM_NO_END_RE.search(text):
        return None
    text = re.sub(r'\s*\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?$', '', text)
    tokens = _TERM_DATE_RE.findall(text)
    if len(tokens) != 1:
        return None
    tok = tokens[0]
    if re.search(r'(?:至|到|止|to|~|～|—|–)\s*' + re.escape(tok), text):
        return None
    if text.replace(tok, '', 1).strip() == '':
        return tok
    return None


def _norm_addr(s: pd.Series) -> pd.Series:
    """地址归一化: 去括号注/申报字样/标点空格/省级前缀, 小写。"""
    s = s.str.replace(r'[（(].*?[)）]', '', regex=True)
    s = s.str.replace(r'自主申报|集群注册|住所申报|一照多址', '', regex=True)
    s = s.str.replace(r'[\s,，。;；、\-—_*·~～#]', '', regex=True).str.lower()
    s = s.str.replace(r'^[一-龥]{2,8}?省', '', regex=True)
    return s


def _ent_type_tier(t: str) -> int:
    """企业类型 -> 治理风险序 (0最稳 ~ 5最弱)。"""
    if t == '-':
        return 4
    if '国有' in t:
        return 0
    if '上市' in t and '非上市' not in t and '未上市' not in t:
        return 0
    if re.search(r'外国|外商|台港澳|中外|港澳台', t):
        return 1
    if '股份' in t:
        return 2
    if '法人独资' in t:
        return 3
    if '自然人独资' in t or '一人有限' in t:
        return 5
    return 4


def _city_tier(city: str) -> int:
    c = re.sub(r'市$', '', city)
    if c in _TIER1:
        return 1
    if c in _NEW_TIER1:
        return 2
    if c in _TIER2:
        return 3
    return 4


# ---------------------------------------------------------------- 主函数
def deep_features(f: pd.DataFrame, as_of, ref: pd.DataFrame = None) -> pd.DataFrame:
    """
    输入: f      原始企业工商信息表(列名为中文原名; 允许缺列)
          as_of  评估基准日(str/Timestamp), 只允许用此日期之前的公开信息
          ref    频次类特征的统计底表(默认=f 本身; 验证集建议传训练集,
                 使小样本验证集也能得到全量口径的集聚度/法人关联计数)
    输出: f 的副本 + DEEP_FEATURE_COLS 数值特征列(无NaN)
    """
    as_of = pd.Timestamp(as_of)
    if pd.isna(as_of):
        raise ValueError('as_of 不能为 NaT')
    if as_of.tzinfo is not None:
        as_of = as_of.tz_localize(None)
    as_of = as_of.normalize()
    out = f.copy()
    if ref is None:
        ref = f

    # ---------- 1. 资本充实度 ----------
    raw_reg, raw_paid = _col(f, '注册资本'), _col(f, '实缴资本')
    reg = _parse_capital(raw_reg)
    paid = _parse_capital(raw_paid)                       # '-' -> NaN
    out['reg_capital_wan'] = reg
    out['paid_capital_wan'] = paid                         # 未披露保持未知，收尾填 -1 哨兵
    # 零注册资本不能作分母；先消除无定义比率，再截断合法比率。
    ratio = (paid / reg.where(reg > 0)).clip(0, 1)
    out['paid_in_ratio'] = ratio                           # 未披露保持未知，不能解释为零
    out['paid_in_disclosed'] = paid.notna().astype(int)
    out['paid_in_full'] = (paid.notna() & (ratio >= 0.95)).astype(int)
    out['log1p_reg_capital'] = np.log1p(reg)
    out['cap_foreign_currency'] = raw_reg.str.contains(
        '美元|欧元|香港元|港元|英镑|日元', regex=True).astype(int)

    # ---------- 2. 成立/核准日期 ----------
    est = _parse_dates(_col(f, '成立日期'))
    app = _parse_dates(_col(f, '核准日期'))
    # 工商事件日在 as_of 之后属于未来信息/脏值：保留诊断但不进入年龄、变更等特征。
    future_est = est.notna() & (est > as_of)
    future_app = app.notna() & (app > as_of)
    out['firm_event_date_after_asof'] = (future_est | future_app).astype(int)
    est = est.mask(future_est)
    app = app.mask(future_app)
    age_y = (as_of - est).dt.days / 365.25
    out['company_age_years'] = age_y
    app_days = (as_of - app).dt.days.astype('float')
    out['approval_days_ago'] = app_days
    out['approval_within_180d'] = (app_days <= 180).fillna(False).astype(int)
    out['approval_within_1y'] = (app_days <= 365).fillna(False).astype(int)
    out['approval_stale_3y'] = (app_days > 1095).fillna(False).astype(int)
    gap = (app - est).dt.days.astype('float')
    out['approval_gap_days'] = gap
    out['never_changed'] = (gap.abs() <= 30).fillna(False).astype(int)

    # ---------- 3. 营业期限 ----------
    term = _col(f, '营业期限')
    term_end = _parse_dates(term.map(_term_end_text))
    # 裸单日期与成立日期相同 → 按"仅起始日"处理，不作终点（防伪造过期）。
    bare = _parse_dates(term.map(_term_bare_single_date))
    term_end = term_end.mask(bare.notna() & est.notna() & (bare == est))
    out['biz_term_disclosed'] = (term != '-').astype(int)
    out['biz_term_expired'] = (term_end < as_of).fillna(False).astype(int)
    out['biz_term_expiring_2y'] = ((term_end >= as_of) &
                                   (term_end < as_of + pd.Timedelta(days=730))).fillna(False).astype(int)
    out['biz_term_remain_years'] = ((term_end - as_of).dt.days / 365.25)   # 无值->fillna 99

    # ---------- 4. 企业类型治理分层 ----------
    ent = _col(f, '企业类型')
    out['ent_type_tier'] = ent.map(_ent_type_tier).astype(int)
    out['is_sole_natural_person'] = ent.str.contains('自然人独资|一人有限', regex=True).astype(int)
    out['is_corp_sole'] = ent.str.contains('法人独资', regex=False).astype(int)
    out['is_foreign'] = ent.str.contains('外国|外商|台港澳|中外|港澳台', regex=True).astype(int)
    out['is_share_co'] = ent.str.contains('股份', regex=False).astype(int)
    out['is_state_owned'] = ent.str.contains('国有', regex=False).astype(int)

    # ---------- 5. 曾用名 / 地址一致性 ----------
    former = _col(f, '曾用名')
    out['has_former_name'] = (former != '-').astype(int)
    ra, ya = _col(f, '注册地址'), _col(f, '最新年报地址')
    na, nb = _norm_addr(ra), _norm_addr(ya)
    both = (ra != '-') & (ya != '-')
    contain = pd.Series(
        [(a in b) or (b in a) if (a and b) else True for a, b in zip(na, nb)],
        index=f.index)
    out['addr_mismatch'] = (both & ~contain).astype(int)
    head_diff = pd.Series([a[:6] != b[:6] for a, b in zip(na, nb)], index=f.index)
    out['addr_mismatch_strong'] = (both & ~contain & head_diff).astype(int)
    out['annual_addr_missing'] = ((ya == '-') & (ra != '-')).astype(int)
    # rename_recent_change 原定义 = has_former_name & approval_within_1y，但两字段语义不匹配：
    # has_former_name 是终生保留（几十年前改名也为1），approval_within_1y 是任意工商
    # 变更（地址/资本/范围/法人任一变更）都会刷新核准日期。该组合在验证集误命中92%
    # （训练集6.5%，漂移+85.5pp），25家大企业核准日期均匀散布近11个月，全是常规变更
    # 登记而非改名。现有字段无法可靠区分"近期改名"与"历史改名+近期其他变更"。
    # 2026-08-07修复：置零该信号，避免在大企业样本（核准日期活跃度高）上误判。
    out['rename_recent_change'] = 0

    # ---------- 6. 场所类型 / 官网 ----------
    out['has_website'] = (_col(f, '网址') != '-').astype(int)
    ind = ra.str.contains(_INDUSTRIAL_KW, regex=True)
    off = ra.str.contains(_OFFICE_KW, regex=True)
    out['addr_industrial'] = ind.astype(int)
    out['addr_office_only'] = (off & ~ind).astype(int)

    # ---------- 7. 经营范围文本 ----------
    sc = _col(f, '经营范围')
    has_sc = sc != '-'
    out['scope_len'] = sc.str.len().where(has_sc, 0)
    out['scope_n_items'] = (sc.str.count('[;；]') + 1).where(has_sc, 0)
    out['scope_new_format'] = sc.str.contains('许可项目|一般项目', regex=True).astype(int)
    out['scope_licensed'] = sc.str.contains('许可项目', regex=False).astype(int)
    out['scope_import_export'] = sc.str.contains(_SCOPE_IMPEX_KW, regex=True).astype(int)
    out['scope_hazard_chem'] = sc.str.contains(_SCOPE_HAZCHEM_KW, regex=True).astype(int)
    out['scope_metal_commodity'] = sc.str.contains(_SCOPE_METAL_KW, regex=True).astype(int)
    out['scope_labor_dispatch'] = sc.str.contains(_SCOPE_LABOR_KW, regex=True).astype(int)
    out['scope_finance_like'] = sc.str.contains(_SCOPE_FINLIKE_KW, regex=True).astype(int)
    out['scope_risk_kw_count'] = sum(sc.str.contains(k, regex=False).astype(int)
                                     for k in _SCOPE_RISK_POOL)

    # ---------- 8. 参保 / 空壳复合分 ----------
    ins_text = _col(f, '参保人数').map(
        lambda value: unicodedata.normalize('NFKC', value))
    ins_text = ins_text.str.replace(',', '', regex=False).str.replace(r'\s', '', regex=True)
    valid_ins = ins_text.str.fullmatch(r'\+?\d+(?:\.0+)?人?')
    ins = pd.to_numeric(ins_text.where(valid_ins).str.removesuffix('人'), errors='coerce')
    ins = ins.where((ins >= 0) & (ins.mod(1) == 0))
    ins = ins.clip(upper=5000)   # 训练集P99.9≈2900, 存在232328人级天眼查脏数据, 封顶防污染
    out['insured_n'] = ins
    out['insured_missing'] = ins.isna().astype(int)
    out['insured_zero'] = (ins == 0).fillna(False).astype(int)
    paid_zero_disclosed = paid.notna() & (ratio <= 0)
    out['shell_flag'] = ((age_y >= 5) & (ins == 0) & paid_zero_disclosed).fillna(False).astype(int)
    website_absent = ((1 - out['has_website']) if '网址' in f.columns else
                      pd.Series(0, index=f.index))
    # 经营范围缺列或占位符都是未知，不能当成“旧格式/低质量经营范围”证据。
    scope_old = ((sc != '-') & (out['scope_new_format'] == 0)).astype(int)
    out['shell_score'] = (out['insured_zero'] + paid_zero_disclosed.astype(int)
                          + out['approval_stale_3y'] + website_absent
                          + scope_old).astype(int)
    out['labor_intensity'] = (ins / (reg / 100.0)).replace([np.inf, -np.inf], np.nan).clip(upper=100)

    # ---------- 9. 区域 ----------
    city = _col(f, '所属城市')
    out['city_tier'] = city.map(_city_tier).astype(int)
    ref_city = _col(ref, '所属城市')
    out['city_firm_cnt'] = city.map(ref_city.value_counts()).where(city != '-')
    district_columns = ['所属城市', '所属区县']
    if '所属省份' in f and '所属省份' in ref:
        district_columns.insert(0, '所属省份')
    district_keys = pd.DataFrame({c: _col(f, c) for c in district_columns})
    ref_district_keys = pd.DataFrame({c: _col(ref, c) for c in district_columns})
    counts = ref_district_keys.loc[ref_district_keys.ne('-').all(axis=1)].value_counts()
    keys = pd.MultiIndex.from_frame(district_keys)
    out['district_firm_cnt'] = pd.Series(
        counts.reindex(keys).to_numpy(), index=f.index).where(district_keys.ne('-').all(axis=1))

    # ---------- 10. 法人关联 ----------
    lr = _col(f, '法定代表人')
    key = lr + '|' + city
    ref_lr = _col(ref, '法定代表人')
    ref_city_for_lr = _col(ref, '所属城市')
    ref_key = ref_lr + '|' + ref_city_for_lr
    cnt = key.map(ref_key.value_counts())
    cnt = cnt.where((lr != '-') & (city != '-'))
    out['legalrep_firm_cnt_city'] = cnt
    out['legalrep_multi_firm_same_city'] = (cnt >= 2).fillna(False).astype(int)
    valid_ref = (ref_lr != '-') & (ref_city_for_lr != '-')
    distinct_city = (pd.DataFrame({'lr': ref_lr[valid_ref], 'city': ref_city_for_lr[valid_ref]})
                     .groupby('lr')['city'].nunique())
    out['legalrep_distinct_city_cnt'] = lr.map(distinct_city).where(lr != '-')
    # 真正跨城同名仅作为诊断；大众姓名可能碰撞，当前不直接计入信号分。
    out['legalrep_multi_city'] = (out['legalrep_distinct_city_cnt'] >= 2).fillna(False).astype(int)

    # ---------- 11. 名称文本 ----------
    nm = _col(f, '企业名称')
    out['name_tech'] = nm.str.contains(_NAME_TECH_KW, regex=True).astype(int)
    out['name_trade'] = nm.str.contains(_NAME_TRADE_KW, regex=True).astype(int)
    out['name_group'] = nm.str.contains(_NAME_GROUP_KW, regex=True).astype(int)
    out['name_len'] = nm.str.len().where(nm != '-', 0)

    # ---------- 收尾: 数值化 + 填充, 保证无NaN ----------
    for c in DEEP_FEATURE_COLS:
        out[c] = pd.to_numeric(out[c], errors='coerce').fillna(_FILL[c])
    return out
