"""Offline upstream-style compaction regression; no real DSH/account payloads."""
import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tool_prompt import scrub_schema, format_tools, format_tool_table, tool_variants
from openai_compat import normalize_request, render_agent_prompt, ProtocolError, parse_calls


def fixtures(count=228):
    return [{'type': 'function', 'function': {'name': f'fixture_{i}', 'description': 'Long descriptive fixture. '*45,
        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'Parameter guidance. '*12,
        'minLength': 1, 'maxLength': 200, 'pattern': '^[a-z/]+$'}}, 'required': ['path'], 'additionalProperties': False}}} for i in range(count)]


def policy(tools):
    return normalize_request({'model':'arena-bound','messages':[{'role':'user','content':'hi'}],'tools':tools})[1]


class CompactionTests(unittest.TestCase):
    def test_228_verbose_tools_fit_without_dropping_names_or_constraints(self):
        tools=fixtures();original=copy.deepcopy(tools);p=policy(tools)
        text=render_agent_prompt([{'role':'user','content':'hi'}],p,200000,utf16=True)
        self.assertGreater(len(json.dumps(tools,ensure_ascii=False,separators=(',',':'))),290000)
        self.assertLessEqual(len(text.encode('utf-16-le'))//2,200000)
        self.assertIn('descriptions shortened',text)
        self.assertEqual(text.count('\n  parameters: '),228)
        for t in tools:self.assertIn('- '+t['function']['name']+':',text)
        params=[json.loads(line.split('parameters: ',1)[1]) for line in text.splitlines() if line.startswith('  parameters: ')]
        for schema in params:
            self.assertEqual(schema['required'],['path'])
            self.assertIs(schema['additionalProperties'],False)
            self.assertEqual(schema['properties']['path']['pattern'],'^[a-z/]+$')
            self.assertEqual(schema['properties']['path']['maxLength'],200)
        self.assertEqual(tools,original)
        self.assertEqual(p.tools,original)

    def test_small_prompt_unchanged_raw_tool_json(self):
        tools=fixtures(1);p=policy(tools)
        text=render_agent_prompt([{'role':'user','content':'hi'}],p,200000)
        self.assertIn(json.dumps(tools,ensure_ascii=False,separators=(',',':')),text)
        self.assertNotIn('descriptions shortened',text)

    def test_schema_keywords_not_whitelisted_away(self):
        s={'type':'object','minProperties':1,'maxProperties':4,'required':['x'],'additionalProperties':False,
           'properties':{'x':{'type':'number','minimum':1,'exclusiveMaximum':20,'multipleOf':2,'description':'long guidance'}},
           'allOf':[{'if':{'properties':{'x':{'const':2}}},'then':{'required':['y']},'else':{'not':{'required':['y']}}}],
           '$defs':{'a':{'type':'array','minItems':1,'maxItems':2,'uniqueItems':True,'prefixItems':[{'enum':[1,2]}]}},
           'patternProperties':{'^a':{'$ref':'#/$defs/a'}},'dependentRequired':{'x':['y']},'unevaluatedProperties':False}
        expected=copy.deepcopy(s);del expected['properties']['x']['description']
        self.assertEqual(scrub_schema(s,0),expected)

    def test_literal_description_fields_and_parameter_names_preserved(self):
        s={'properties':{'description':{'type':'string','description':'annotation'}},
           'enum':[{'description':'literal'}],'const':{'description':'literal'},'default':{'description':'literal'},
           'examples':[{'description':'literal'}],'x-vendor':{'description':'opaque'}}
        r=scrub_schema(s,0)
        self.assertEqual(r['properties']['description'],{'type':'string'})
        for k in ('enum','const','default','examples','x-vendor'):self.assertEqual(r[k],s[k])

    def test_boolean_schemas_and_dependencies_preserved(self):
        s={'additionalProperties':True,'properties':{'x':False},'dependencies':{'x':['y'],'y':{'description':'d','required':['z']}},'items':[True,{'description':'d','type':'integer'}]}
        r=scrub_schema(s,0)
        self.assertTrue(r['additionalProperties']);self.assertIs(r['properties']['x'],False)
        self.assertEqual(r['dependencies']['x'],['y']);self.assertEqual(r['dependencies']['y'],{'required':['z']})
        self.assertEqual(r['items'],[True,{'type':'integer'}])

    def test_zero_cap_removes_parameter_descriptions(self):
        tool=fixtures(1)[0];out=format_tools([tool],0,0)
        self.assertNotIn('Long descriptive',out);self.assertNotIn('Parameter guidance',out)

    def test_irreducible_enum_rejected_no_truncated_definition(self):
        tools=fixtures(1);tools[0]['function']['parameters']['enum']=['x'*210000]
        original=copy.deepcopy(tools)
        with self.assertRaises(ProtocolError) as c:render_agent_prompt([{'role':'user','content':'hi'}],policy(tools),200000)
        self.assertEqual(c.exception.code,'context_length_exceeded');self.assertIn('compact_tool_schema=',str(c.exception))
        self.assertEqual(tools,original)

    def test_history_is_not_trimmed_to_make_room(self):
        messages=[{'role':'system','content':'x'*200001},{'role':'user','content':'hi'}]
        with self.assertRaises(ProtocolError):render_agent_prompt(messages,policy(fixtures()),200000)

    def test_call_result_ids_remain_paired_in_full_history(self):
        msgs=[{'role':'user','content':'do it'},{'role':'assistant','content':None,'tool_calls':[{'id':'call_fixture','type':'function','function':{'name':'fixture_0','arguments':'{"path":"a"}'}}]},
              {'role':'tool','tool_call_id':'call_fixture','name':'fixture_0','content':'fixture observed result'}]
        b,p,_=normalize_request({'model':'arena-bound','tools':fixtures(),'messages':msgs})
        text=render_agent_prompt(b['messages'],p,200000)
        restored=json.loads(text.split('Conversation in chronological order (tool results are client observations):\n',1)[1])
        self.assertEqual(restored,b['messages'])
        self.assertEqual(restored[1]['tool_calls'][0]['id'],restored[2]['tool_call_id'])

    def test_selection_parallel_and_return_validation_preserved(self):
        b,p,_=normalize_request({'model':'arena-bound','tools':fixtures(),'messages':[{'role':'user','content':'hi'}], 'parallel_tool_calls':False})
        text=render_agent_prompt(b['messages'],p,200000)
        self.assertIn('Return at most ONE',text)
        self.assertEqual(parse_calls('<tool_call>{"name":"fixture_227","arguments":{"path":"a"}}</tool_call>',p)[0]['function']['name'],'fixture_227')
        with self.assertRaises(ProtocolError):parse_calls('<tool_call>{"name":"missing_tool","arguments":{}}</tool_call>',p)

    def test_utf16_overflow_never_slips_through(self):
        with self.assertRaises(ProtocolError):render_agent_prompt([{'role':'user','content':'😀'*100001}],policy(fixtures(1)),200000,utf16=True)

class TableTests(unittest.TestCase):
    def test_table_roundtrip_preserves_schema_literals_and_references(self):
        tools=fixtures(3)
        tools[0]['function']['parameters'].update({'$id':'urn:fixture', '$ref':'#/$defs/a', '$defs':{'a':{'const':{'description':'literal'}}}})
        original=copy.deepcopy(tools)
        rows=json.loads(format_tool_table(tools).split('\n',1)[1])
        self.assertEqual(rows,[[t['function']['name'],scrub_schema(t['function']['parameters'],0)] for t in tools])
        self.assertEqual(tools,original)

    def test_screenshot_sized_unique_schemas_fit_including_history(self):
        tools=fixtures()
        # Synthetic unique schemas calibrated to screenshot's LAST-stage size,
        # not an assertion about the real DSH payload contents.
        for t in tools:t['function']['parameters']['const']=t['function']['name']
        delta=201200-len(format_tools(tools,0,0))
        tools[0]['function']['parameters']['const']+='x'*delta
        self.assertEqual(len(format_tools(tools,0,0)),201200)
        messages=[{'role':'system','content':'x'*46},{'role':'user','content':'hi'}]
        history='Conversation in chronological order (tool results are client observations):\n'+json.dumps(messages,ensure_ascii=False,separators=(',',':'))
        self.assertEqual(len(history),185)
        text=render_agent_prompt(messages,policy(tools),200000,utf16=True)
        self.assertLessEqual(len(text.encode('utf-16-le'))//2,200000)
        self.assertIn('Tool table: each row',text)
        table=text.split('Tool table: each row is [function_name, complete parameters schema].\n',1)[1].split('\n\n',1)[0]
        rows=json.loads(table)
        self.assertEqual(len(rows),228)
        for row,t in zip(rows,tools):self.assertEqual(row,[t['function']['name'],scrub_schema(t['function']['parameters'],0)])

    def test_identical_whole_schemas_shared_and_recoverable(self):
        tools=fixtures();table=json.loads(format_tool_table(tools,deduplicate=True).split('\n',1)[1])
        self.assertEqual(len(table['schemas']),1)
        self.assertEqual(len(table['tools']),228)
        for row,t in zip(table['tools'],tools):
            self.assertEqual(row[0],t['function']['name'])
            self.assertEqual(table['schemas'][row[1]],scrub_schema(t['function']['parameters'],0))
        text=render_agent_prompt([{'role':'user','content':'hi'}],policy(tools),15000,utf16=True)
        self.assertIn('zero-based index into schemas',text)
        self.assertLessEqual(len(text.encode('utf-16-le'))//2,15000)

    def test_unique_schema_table_does_not_force_larger_shared_form(self):
        tools=fixtures(10)
        for i,t in enumerate(tools):t['function']['parameters']['const']=i
        variants=list(tool_variants(tools))
        self.assertEqual(variants[-1],format_tool_table(tools))

    def test_astral_names_and_literal_schema_values_roundtrip(self):
        tools=fixtures(1);tools[0]['function']['parameters']['const']='😀'*250
        rows=json.loads(format_tool_table(tools).split('\n',1)[1])
        self.assertEqual(rows[0][1]['const'],'😀'*250)
        with self.assertRaises(ProtocolError):render_agent_prompt([{'role':'user','content':'😀'*100001}],policy(tools),200000,utf16=True)

if __name__=='__main__':unittest.main(verbosity=2)
