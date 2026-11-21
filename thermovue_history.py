"""Durable sensor archive, stable pagination, real BIFF XLS and PDF exports."""
import io
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WIB = ZoneInfo('Asia/Jakarta')
DB = Path('/home/admin/.local/state/thermovue/readings.sqlite3')
COLUMNS = ['No.', 'Waktu (WIB)', 'Sensor', 'ID Modbus', 'Suhu (°C)', 'Bawah (°C)', 'Atas (°C)', 'Status']

def filter_args(args):
    values = {}
    for key in ('start','end'):
        text = args.get(key, '')
        if not text:
            values[key] = None
            continue
        try:
            date = datetime.fromisoformat(text)
            if date.tzinfo is None:
                date = date.replace(tzinfo=WIB)
            values[key] = date.timestamp()
            # Include samples with fractional timestamps throughout the final second.
            if key == 'end' and date.microsecond == 0:
                values[key] += .999999
            if not math.isfinite(values[key]):
                raise ValueError()
        except (ValueError, OverflowError):
            raise ValueError('Tanggal dan jam tidak valid. Gunakan format tanggal-jam WIB.')
    if values['start'] is not None and values['end'] is not None and values['start'] > values['end']:
        raise ValueError('Tanggal/jam awal harus lebih awal atau sama dengan tanggal/jam akhir.')
    try:
        snapshot = args.get('snapshot')
        values['snapshot'] = int(snapshot) if snapshot not in (None, '') else None
        if values['snapshot'] is not None and values['snapshot'] < 0:
            raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('Snapshot riwayat tidak valid.')
    return values

class HistoryStore:
    def __init__(self, path=DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY, epoch REAL NOT NULL, sensor INTEGER NOT NULL,
                device_id INTEGER NOT NULL, temperature REAL NOT NULL,
                low_alarm REAL, high_alarm REAL, status TEXT NOT NULL,
                UNIQUE(epoch,sensor))''')
            conn.execute('CREATE INDEX IF NOT EXISTS readings_time ON readings(epoch DESC,id DESC)')

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path,timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def append(self, rows):
        if not rows:return
        with self.connection() as conn:
            conn.executemany('''INSERT OR IGNORE INTO readings
                (epoch,sensor,device_id,temperature,low_alarm,high_alarm,status)
                VALUES (?,?,?,?,?,?,?)''', rows)

    def latest_times(self):
        with self.connection() as conn:
            times = {row['sensor']:row['epoch'] for row in conn.execute(
                'SELECT sensor, MAX(epoch) AS epoch FROM readings GROUP BY sensor')}
        return [times.get(1,0),times.get(2,0)]

    def preserve_memory(self, state):
        # Old in-memory samples have no recorded historical limits. Keep these unknown.
        cfg = state['config']
        rows = []
        for i,history in enumerate(state['history']):
            device = cfg['modbus']['slave_id' if i == 0 else 'sensor2_slave_id']
            for stamp,raw in history:
                rows.append((stamp,i+1,device,raw+cfg[f'sensor{i+1}'].get('offset',0),None,None,'Tidak tercatat'))
        self.append(rows)

    @staticmethod
    def public(row):
        result = dict(row)
        result['time'] = datetime.fromtimestamp(row['epoch'],WIB).strftime('%Y-%m-%d %H:%M:%S')
        return result

    def query(self, *, start=None, end=None, snapshot=None, page=1, page_size=10, export=False):
        with self.connection() as conn:
            if snapshot is None:
                snapshot = conn.execute('SELECT COALESCE(MAX(id),0) FROM readings').fetchone()[0]
            where,params = ['id <= ?'],[snapshot]
            if start is not None:where.append('epoch >= ?');params.append(start)
            if end is not None:where.append('epoch <= ?');params.append(end)
            sql = ' FROM readings WHERE '+' AND '.join(where)
            total = conn.execute('SELECT COUNT(*)'+sql,params).fetchone()[0]
            pages = max(1,math.ceil(total/page_size))
            page = min(page,pages)
            if export:
                if total > 50000:
                    raise ValueError('Ekspor maksimal 50.000 baris per file. Persempit tanggal/jam agar seluruh data pilihan dapat diunduh.')
                rows = conn.execute('SELECT *'+sql+' ORDER BY epoch DESC,id DESC',params).fetchall()
            else:
                rows = conn.execute('SELECT *'+sql+' ORDER BY epoch DESC,id DESC LIMIT ? OFFSET ?',
                                    params+[page_size,(page-1)*page_size]).fetchall()
        return dict(rows=[self.public(r) for r in rows],total=total,page=page,page_size=page_size,
                    pages=pages,snapshot=snapshot,timezone='Asia/Jakarta')

def export_values(row,index):
    return [index,row['time'],f"Sensor {row['sensor']}",row['device_id'],row['temperature'],
            row['low_alarm'],row['high_alarm'],row['status']]

def period_text(start,end):
    fmt = lambda v: datetime.fromtimestamp(v,WIB).strftime('%d/%m/%Y %H:%M:%S') if v is not None else 'Tanpa batas'
    return f'{fmt(start)} - {fmt(end)} WIB'

def make_xls(rows,period):
    import xlwt
    book = xlwt.Workbook(encoding='utf-8')
    title = xlwt.easyxf('font: bold on, height 320, colour white; pattern: pattern solid, fore_colour dark_teal;')
    header = xlwt.easyxf('font: bold on, colour white; pattern: pattern solid, fore_colour dark_teal; align: vert centre;')
    date_style = xlwt.easyxf(num_format_str='DD/MM/YYYY HH:MM:SS')
    numeric = xlwt.easyxf(num_format_str='0.0')
    sheet = book.add_sheet('Data suhu')
    sheet.write_merge(0,0,0,7,'ThermoVue | Riwayat suhu PT100',title)
    sheet.write_merge(1,1,0,7,period)
    sheet.write_merge(2,2,0,7,f'{len(rows)} baris | Semua halaman hasil filter | Waktu Asia/Jakarta (WIB)')
    for col,name in enumerate(COLUMNS):
        sheet.write(4,col,name,header)
        sheet.col(col).width = [7,24,14,13,14,15,15,23][col]*256
    sheet.set_panes_frozen(True);sheet.set_horz_split_pos(5)
    for index,row in enumerate(rows,1):
        for col,value in enumerate(export_values(row,index)):
            if col == 1:
                value = datetime.fromtimestamp(row['epoch'],WIB).replace(tzinfo=None)
                sheet.write(index+4,col,value,date_style)
            elif col in (4,5,6) and value is not None:
                sheet.write(index+4,col,value,numeric)
            else:
                sheet.write(index+4,col,'' if value is None else value)
    stream=io.BytesIO();book.save(stream)
    return stream.getvalue()

def make_pdf(rows,period):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
    if 'ThermoSans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('ThermoSans','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    stream=io.BytesIO()
    doc=SimpleDocTemplate(stream,pagesize=landscape(A4),rightMargin=25,leftMargin=25,
                          topMargin=27,bottomMargin=32,title='ThermoVue - Riwayat suhu PT100')
    styles=getSampleStyleSheet()
    for style in styles.byName.values():style.fontName='ThermoSans'
    data=[COLUMNS]
    for index,row in enumerate(rows,1):
        data.append(['—' if v is None else f'{v:.1f}' if isinstance(v,float) and col>=4 else str(v)
                     for col,v in enumerate(export_values(row,index))])
    table=LongTable(data,repeatRows=1,colWidths=[32,141,65,65,76,76,76,120])
    table.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'ThermoSans'),('FONTSIZE',(0,0),(-1,-1),8),
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#183b39')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#eef3ec')]),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#183b39'))]))
    story=[Paragraph('ThermoVue | Riwayat suhu PT100',styles['Title']),
           Paragraph(period,styles['Normal']),Spacer(1,8),
           Paragraph(f'{len(rows)} baris • Semua halaman hasil filter • Waktu WIB',styles['Normal']),Spacer(1,14),table]
    if not rows:story.append(Paragraph('Tidak ada data pada periode ini.',styles['Normal']))
    def footer(canvas,document):
        canvas.setFont('ThermoSans',8)
        canvas.drawString(26,16,'ThermoVue • PT100 • Batas kosong berarti belum tercatat saat pengukuran.')
        canvas.drawRightString(815,16,f'Halaman {document.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
