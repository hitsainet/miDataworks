// Feature 020 (FR-020.10, TPD-15): agent actor labels, including the ids added by the rename.
import { describe, expect, it } from 'vitest';

import { whoText } from './runSummary';

const agent = (started_by: string) => ({ started_by, started_by_origin: 'agent' as const });

describe('whoText', () => {
  it('labels agent:miforge as miForge', () => {
    expect(whoText(agent('agent:miforge'))).toBe('an AI agent (through miForge)');
  });

  it("labels both MCP actor ids as miWorkbench's MCP server", () => {
    // P4 (FR-020.10, Q3): the old id is frozen (register DR-17) and still arrives on old rows; it
    // reads like the new one.
    expect(whoText(agent('agent:miworkbench-mcp'))).toBe("an AI agent (through miWorkbench's MCP server)");
    expect(whoText(agent('agent:mistudio-mcp'))).toBe("an AI agent (through miWorkbench's MCP server)");
  });

  it('names an operator by name', () => {
    expect(whoText({ started_by: 'Ada', started_by_origin: 'operator' })).toBe('Ada');
  });
});
