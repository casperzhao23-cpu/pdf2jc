"""Create a small PowerPoint file using only Python's standard library."""

from __future__ import annotations

from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_pptx(slide_plan: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(output_path, "w", ZIP_DEFLATED) as pptx:
        pptx.writestr("[Content_Types].xml", _content_types_xml(len(slide_plan)))
        pptx.writestr("_rels/.rels", _package_rels_xml())
        pptx.writestr("docProps/app.xml", _app_xml(len(slide_plan)))
        pptx.writestr("docProps/core.xml", _core_xml())
        pptx.writestr("ppt/presentation.xml", _presentation_xml(len(slide_plan)))
        pptx.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels_xml(len(slide_plan)))

        for index, slide in enumerate(slide_plan, start=1):
            pptx.writestr(f"ppt/slides/slide{index}.xml", _slide_xml(slide))
            pptx.writestr(
                f"ppt/slides/_rels/slide{index}.xml.rels",
                _empty_relationships_xml(),
            )


def _content_types_xml(slide_count: int) -> str:
    slide_overrides = "\n".join(
        f'  <Override PartName="/ppt/slides/slide{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
{slide_overrides}
</Types>
'''


def _package_rels_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PACKAGE_REL_NS}">
  <Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="ppt/presentation.xml"/>
  <Relationship Id="rId2" Type="{REL_NS}/extended-properties" Target="docProps/app.xml"/>
  <Relationship Id="rId3" Type="{REL_NS}/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>
'''


def _presentation_rels_xml(slide_count: int) -> str:
    relationships = "\n".join(
        f'  <Relationship Id="rId{i}" Type="{REL_NS}/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PACKAGE_REL_NS}">
{relationships}
</Relationships>
'''


def _empty_relationships_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PACKAGE_REL_NS}"/>
'''


def _presentation_xml(slide_count: int) -> str:
    slide_ids = "\n".join(
        f'    <p:sldId id="{255 + i}" r:id="rId{i}"/>'
        for i in range(1, slide_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="{PRESENTATION_NS}" xmlns:a="{DRAWING_NS}" xmlns:r="{REL_NS}">
  <p:sldIdLst>
{slide_ids}
  </p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>
'''


def _slide_xml(slide: dict) -> str:
    title = escape(str(slide["title"]))
    bullets = [escape(str(bullet)) for bullet in slide["bullets"]]
    bullet_runs = "\n".join(
        f'''        <a:p>
          <a:pPr marL="342900" indent="-228600">
            <a:buChar char="•"/>
          </a:pPr>
          <a:r>
            <a:rPr lang="en-US" sz="2400"/>
            <a:t>{bullet}</a:t>
          </a:r>
        </a:p>'''
        for bullet in bullets
    )

    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="{PRESENTATION_NS}" xmlns:a="{DRAWING_NS}" xmlns:r="{REL_NS}">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x="0" y="0"/>
          <a:ext cx="0" cy="0"/>
          <a:chOff x="0" y="0"/>
          <a:chExt cx="0" cy="0"/>
        </a:xfrm>
      </p:grpSpPr>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Title"/>
          <p:cNvSpPr txBox="1"/>
          <p:nvPr/>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm>
            <a:off x="685800" y="365760"/>
            <a:ext cx="10820400" cy="822960"/>
          </a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        </p:spPr>
        <p:txBody>
          <a:bodyPr wrap="square"/>
          <a:lstStyle/>
          <a:p>
            <a:r>
              <a:rPr lang="en-US" sz="3600" b="1"/>
              <a:t>{title}</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="3" name="Bullets"/>
          <p:cNvSpPr txBox="1"/>
          <p:nvPr/>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm>
            <a:off x="914400" y="1543050"/>
            <a:ext cx="10363200" cy="4572000"/>
          </a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        </p:spPr>
        <p:txBody>
          <a:bodyPr wrap="square"/>
          <a:lstStyle/>
{bullet_runs}
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr>
    <a:masterClrMapping/>
  </p:clrMapOvr>
</p:sld>
'''


def _app_xml(slide_count: int) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>pdf2jc</Application>
  <PresentationFormat>On-screen Show (16:9)</PresentationFormat>
  <Slides>{slide_count}</Slides>
</Properties>
'''


def _core_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>pdf2jc mock journal club draft</dc:title>
  <dc:creator>pdf2jc</dc:creator>
</cp:coreProperties>
'''

