MESSAGES = {
    'SAMPLE_UNKNOWN':'没有这个样例', 'BAD_PAYLOAD':'请提交一个样例名或 CSV 文本', 'DECODE':'无法读取文件',
    'EMPTY_FILE':'没有文件内容', 'BLANK_FILE':'文件只有空白行', 'NO_ROWS':'表头下没有申请',
    'HEADER':'列名与收件规范不符', 'WIDTH':'列数与表头不符', 'MISSING':'必填字段缺失',
    'INVALID':'日期、动作或页段格式无效', 'BLANK_ROW':'空白行', 'ID_COLLISION':'同流水号材料内容互斥',
    'UNKNOWN_ISSN':'刊号未登记', 'UNKNOWN_ORG':'机构号未登记', 'VERSION_TIE':'同级条款无法裁决',
    'NO_LICENSE':'当天无有效授权', 'OUTSIDE_ARTICLE':'申请超出文章页界', 'QUOTA':'额度不足',
    'UNKNOWN_TARGET':'撤销目标未裁决', 'REPEATED_CANCEL':'复本订单已撤销', 'WRONG_ORG':'撤销机构与原件不符',
    'RUN_UNKNOWN':'未找到许可批次', 'DRAFT_ONLY':'请先签发批次凭证', 'BOUNDARY':'跨年度复本不能撤销',
    'PERIOD_FORMAT':'月份格式应为 YYYY-MM', 'PERIOD_CLOSED':'该月份已结账，数字已冻结',
    'PERIOD_ORDER':'须按月份顺序结账', 'PERIOD_BUSY':'该月份正在结账，请重取结果',
    'FUTURE_PERIOD':'不能对未来月份结账', 'CLOSE_UNKNOWN':'该月份尚未结账',
}


class DeskError(ValueError):
    def __init__(self, code, status=422):
        super().__init__(MESSAGES[code])
        self.code, self.status = code, status
