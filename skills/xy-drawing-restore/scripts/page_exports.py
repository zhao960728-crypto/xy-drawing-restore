# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Readback checks for independent page DXFs."""
import hashlib
import json


PAGE_HEADER_VARS = ('$MEASUREMENT', '$LTSCALE', '$CELTSCALE', '$LWDISPLAY', '$FILLMODE', '$PDMODE', '$PDSIZE')


def _payload(entity, stack=()):
    from ezdxf.lldxf.tagwriter import TagCollector
    doc=entity.doc
    def table(record, depth=0):
        if record is None:return None
        if depth>8:raise ValueError('Cyclic CAD resource references')
        collector=TagCollector(dxfversion=doc.dxfversion)
        record.export_dxf(collector)
        data={k:v for k,v in record.dxfattribs().items() if k not in {'name','handle','owner'}}
        for key in ('dimtxsty','dimblk','dimblk1','dimblk2','dimltype','dimltex1','dimltex2'):
            value=data.get(key);handle=data.pop(key+'_handle',None)
            if value:
                if key=='dimtxsty':data[key]=table(doc.styles.get(value),depth+1)
                elif key.startswith('dimblk'):data[key]=block(doc.blocks.get(value))
                else:data[key]=table(doc.linetypes.get(value),depth+1)
            elif handle and handle in doc.entitydb:
                target=doc.entitydb[handle]
                data[key]=block(doc.blocks.get(target.dxf.name)) if target.dxftype()=='BLOCK_RECORD' else table(target,depth+1)
        if record.dxftype()=='LAYER' and data.get('linetype'):
            data['linetype']=table(doc.linetypes.get(data['linetype']),depth+1)
        # Pattern values matter; record IDs and renamed table entry names do not.
        if record.dxftype()=='LTYPE':
            data['pattern']=[(t.code,str(t.value)) for t in collector.tags
                             if t.code not in {2,5,105,*range(320,370),*range(390,400)}]
        return data
    def block(layout):
        if layout is None:return None
        name=layout.name
        if name in stack:raise ValueError('Cyclic CAD blocks cannot be exported')
        return {'base':list(layout.block.dxf.base_point),
                'entities':[_payload(e,(*stack,name)) for e in layout.entities_in_redraw_order()]}
    def dimension_overrides():
        overrides = dict(entity.override().dimstyle_attribs)
        for key in ('dimtxsty', 'dimblk', 'dimblk1', 'dimblk2', 'dimldrblk',
                    'dimltype', 'dimltex1', 'dimltex2'):
            name = overrides.get(key)
            if not name:
                continue
            if key == 'dimtxsty':
                overrides[key] = table(doc.styles.get(name))
            elif key in {'dimblk', 'dimblk1', 'dimblk2', 'dimldrblk'}:
                layout = doc.blocks.get(name)
                if layout is None:
                    layout = doc.blocks.get('_' + name)
                overrides[key] = block(layout) if layout is not None else name
            else:
                overrides[key] = table(doc.linetypes.get(name))
        return overrides
    collector=TagCollector(dxfversion=doc.dxfversion)
    entity.export_dxf(collector)
    skip={5,105,1005,*range(320,370),*range(390,400),480,481}
    kind=entity.dxftype();tags=[]
    for t in collector.tags:
        if t.code in skip:continue
        if t.code==2 and kind in {'INSERT','DIMENSION'}:continue
        if t.code==3 and kind=='DIMENSION':continue
        if t.code == 8:
            value = {'inherits_insert_layer': t.value == '0', 'layer': table(doc.layers.get(str(t.value)))}
        elif t.code==7 and kind in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:value=table(doc.styles.get(str(t.value)))
        elif t.code==6 and str(t.value).upper() not in {'BYLAYER','BYBLOCK'}:value=table(doc.linetypes.get(str(t.value)))
        else:value=str(t.value)
        tags.append((t.code,value))
    result={'type':kind,'tags':tags}
    if kind=='INSERT':
        result['block']=block(doc.blocks.get(entity.dxf.name))
        result['attributes']=[_payload(a,stack) for a in entity.attribs]
    elif kind=='DIMENSION':
        result['display']=block(entity.get_geometry_block())
        result['style']=table(doc.dimstyles.get(entity.dxf.dimstyle))
        result['overrides'] = dimension_overrides()
    elif kind=='POLYLINE':result['vertices']=[_payload(v,stack) for v in entity.vertices]
    return result


def _signature(entity):
    return hashlib.sha256(json.dumps(_payload(entity),sort_keys=True,default=str).encode()).hexdigest()


def verify_page_dxf(document, redraw_entities, path):
    import ezdxf

    expected = [_signature(entity) for entity in redraw_entities]
    reopened = ezdxf.readfile(str(path))
    audit = reopened.audit()
    if audit.has_errors or audit.has_fixes or reopened.units != document.units:
        raise ValueError('Saved page DXF requires repair or changed units.')
    changed = [name for name in PAGE_HEADER_VARS if reopened.header.get(name) != document.header.get(name)]
    if changed:
        raise ValueError('Saved page DXF changed drawing display settings: ' + ', '.join(changed))
    actual = [_signature(entity) for entity in reopened.modelspace().entities_in_redraw_order()]
    if actual != expected:
        raise ValueError('Saved page DXF changed geometry, styles, blocks or dimension display.')
    return reopened


def write_page_dxf(document, ordered_handles, redraw_entities, path):
    """Write and reopen one page, preserving the master geometry and draw order."""
    import ezdxf
    from ezdxf import xref

    wanted = set(ordered_handles)
    target = ezdxf.new(document.dxfversion)
    target.units = document.units
    for name in PAGE_HEADER_VARS:
        if name in document.header:
            target.header[name] = document.header[name]
    # ezdxf reserves layer 0 and does not import it with modelspace entities.
    # Its name must survive because block contents inherit the INSERT's layer.
    source_layer = document.layers.get('0')
    target_layer = target.layers.get('0')
    for name in ('flags', 'color', 'true_color', 'linetype', 'plot', 'lineweight', 'unknown1'):
        if source_layer.dxf.hasattr(name):
            target_layer.dxf.set(name, source_layer.dxf.get(name))
    loader = xref.Loader(document, target, conflict_policy=xref.ConflictPolicy.NUM_PREFIX)
    if source_layer.dxf.linetype not in target.linetypes:
        loader.load_linetypes([source_layer.dxf.linetype])
    loader.load_modelspace(filter_fn=lambda entity: entity.dxf.handle in wanted)
    loader.execute()
    model = target.modelspace()
    imported = list(model)
    if len(imported) != len(ordered_handles):
        raise ValueError('Page extraction omitted CAD entities.')
    mapping = {handle: entity.dxf.handle for handle, entity in zip(ordered_handles, imported)}
    model.set_redraw_order([(mapping[entity.dxf.handle], f'{i:X}')
                            for i, entity in enumerate(redraw_entities, 1)])
    target.saveas(str(path))
    return verify_page_dxf(document, redraw_entities, path)
