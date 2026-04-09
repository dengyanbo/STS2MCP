using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Godot;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;

namespace STS2_MCP;

public static partial class McpMod
{
    // ── Card Effect Classification ─────────────────────────────────────
    // Whitelist: card.Id.Entry → known effect tags.
    // Checked first; description keyword fallback only for unlisted cards.
    private static readonly Dictionary<string, string[]> CardEffectMap = new(StringComparer.OrdinalIgnoreCase)
    {
        // Ironclad — debuffs
        ["Bash"]            = new[] { "applies_vulnerable" },
        ["Thunderclap"]     = new[] { "applies_vulnerable" },
        ["Uppercut"]        = new[] { "applies_vulnerable", "applies_weak" },
        ["Disarm"]          = new[] { "applies_weak" },
        ["Clothesline"]     = new[] { "applies_weak" },
        ["Shockwave"]       = new[] { "applies_vulnerable", "applies_weak" },
        ["Intimidate"]      = new[] { "applies_weak" },

        // Ironclad — draw / card generation
        ["Offering"]        = new[] { "draws_cards", "gains_energy", "has_randomness" },
        ["BurningPact"]     = new[] { "draws_cards", "exhausts_cards", "has_randomness" },
        ["BattleTrance"]    = new[] { "draws_cards" },
        ["ShrugItOff"]      = new[] { "draws_cards", "gains_block" },
        ["PommelStrike"]    = new[] { "draws_cards" },
        ["Immolate"]        = new[] { "draws_cards" },
        ["SecondWind"]      = new[] { "exhausts_cards", "gains_block" },
        ["DarkEmbrace"]     = new[] { "draws_cards", "is_power" },
        ["Brutality"]       = new[] { "draws_cards", "is_power" },
        ["Juggernaut"]      = new[] { "is_power" },
        ["Havoc"]           = new[] { "has_randomness" },

        // Ironclad — strength / energy
        ["Inflame"]         = new[] { "gains_strength", "is_power" },
        ["DemonForm"]       = new[] { "gains_strength", "is_power" },
        ["SpotWeakness"]    = new[] { "gains_strength" },
        ["LimitBreak"]      = new[] { "gains_strength" },
        ["Bloodletting"]    = new[] { "gains_energy" },
        ["SeeingRed"]       = new[] { "gains_energy", "exhausts_cards" },
        ["Berserk"]         = new[] { "gains_energy", "is_power" },

        // Ironclad — block
        ["Defend_R"]        = new[] { "gains_block" },
        ["TrueGrit"]        = new[] { "gains_block", "exhausts_cards" },
        ["Impervious"]      = new[] { "gains_block", "exhausts_cards" },
        ["GhostlyArmor"]    = new[] { "gains_block", "exhausts_cards" },
        ["FlameBarrier"]    = new[] { "gains_block" },
        ["Metallicize"]     = new[] { "gains_block", "is_power" },
        ["Barricade"]       = new[] { "is_power" },
        ["Entrench"]        = new[] { "gains_block" },

        // Ironclad — exhaust synergy
        ["Corruption"]      = new[] { "is_power" },
        ["FeelNoPain"]      = new[] { "gains_block", "is_power" },
        ["DarkEmbrace_FNP"] = new[] { "draws_cards", "is_power" },
        ["Sentinel"]        = new[] { "gains_block", "gains_energy", "exhausts_cards" },
        ["Exhume"]          = new[] { "has_randomness" },

        // Ironclad — X-cost
        ["Whirlwind"]       = new[] { "is_x_cost" },
        ["FiendFire"]       = new[] { "is_x_cost", "exhausts_cards" },

        // Generic / multi-class
        ["Cleave"]          = new string[] { },
        ["Strike_R"]        = new string[] { },
        ["TwinStrike"]      = new string[] { },
        ["HeavyBlade"]      = new string[] { },
        ["Carnage"]         = new string[] { },
        ["Bludgeon"]        = new string[] { },
        ["Rampage"]         = new string[] { },
        ["BodySlam"]        = new string[] { },
        ["Headbutt"]        = new string[] { },
        ["Anger"]           = new string[] { },
        ["Clash"]           = new string[] { },
        ["Armaments"]       = new string[] { },
        ["Flex"]            = new[] { "gains_strength" },
        ["Warcry"]          = new[] { "draws_cards" },
        ["RecklessCharge"]  = new[] { "draws_cards" },

        // Potions that buff (handled separately, but kept for consistency)
    };

    /// <summary>
    /// Classify a hand card into structured effect tags.
    /// Priority: whitelist by Id → fallback keyword scan on description.
    /// </summary>
    internal static List<string> ClassifyCardEffects(CardModel card)
    {
        // 1. Whitelist lookup — strip trailing "+" from upgraded id
        string rawId = card.Id.Entry;
        string baseId = rawId.EndsWith("+") ? rawId[..^1] : rawId;

        if (CardEffectMap.TryGetValue(baseId, out var mapped))
        {
            var tags = new List<string>(mapped);
            // Supplement with structural checks not in the map
            AddStructuralTags(card, tags);
            return tags;
        }

        // 2. Fallback: description keyword scan
        string desc = SafeGetCardDescription(card) ?? "";
        var fallback = new List<string>();

        if (desc.Contains("易伤") || desc.Contains("Vulnerable"))   fallback.Add("applies_vulnerable");
        if (desc.Contains("虚弱") || desc.Contains("Weak"))         fallback.Add("applies_weak");
        if (Regex.IsMatch(desc, @"抽\s*\d+\s*张|[Dd]raw\s+\d+"))   fallback.Add("draws_cards");
        if (desc.Contains("力量") && !desc.Contains("失去力量"))      fallback.Add("gains_strength");
        if (desc.Contains("能量") || desc.Contains("[Energy]"))      fallback.Add("gains_energy");
        if (desc.Contains("格挡") || desc.Contains("Block"))        fallback.Add("gains_block");
        if (desc.Contains("消耗") || desc.Contains("Exhaust"))      fallback.Add("exhausts_cards");
        if (desc.Contains("随机") || desc.Contains("Random"))       fallback.Add("has_randomness");

        // Draw implies randomness (new cards from draw pile)
        if (fallback.Contains("draws_cards") && !fallback.Contains("has_randomness"))
            fallback.Add("has_randomness");

        AddStructuralTags(card, fallback);
        return fallback;
    }

    private static void AddStructuralTags(CardModel card, List<string> tags)
    {
        if (card.Type == CardType.Power && !tags.Contains("is_power"))
            tags.Add("is_power");
        if (card.EnergyCost.CostsX && !tags.Contains("is_x_cost"))
            tags.Add("is_x_cost");

        int cost = card.EnergyCost.CostsX ? 99 : card.EnergyCost.GetAmountToSpend();
        if (cost == 0 && !tags.Contains("is_free"))
            tags.Add("is_free");
    }
    private static string? SafeGetCardDescription(CardModel card, PileType pile = PileType.Hand)
    {
        try { return StripRichTextTags(card.GetDescriptionForPile(pile)).Replace("\n", " "); }
        catch { return SafeGetText(() => card.Description)?.Replace("\n", " "); }
    }

    internal static string? SafeGetText(Func<object?> getter)
    {
        try
        {
            var result = getter();
            if (result == null) return null;
            // If it's a LocString, call GetFormattedText
            if (result is MegaCrit.Sts2.Core.Localization.LocString locString)
                return StripRichTextTags(locString.GetFormattedText());
            return result.ToString();
        }
        catch { return null; }
    }

    internal static string StripRichTextTags(string text)
    {
        // Remove BBCode-style tags like [color=red], [/color], etc.
        // Special case: [img]res://path/to/file.png[/img] → [file.png]
        var sb = new StringBuilder();
        int i = 0;
        while (i < text.Length)
        {
            if (text[i] == '[')
            {
                // Check for [img]...[/img] pattern
                if (text.AsSpan(i).StartsWith("[img]"))
                {
                    int contentStart = i + 5; // length of "[img]"
                    int closeTag = text.IndexOf("[/img]", contentStart, StringComparison.Ordinal);
                    if (closeTag >= 0)
                    {
                        string path = text[contentStart..closeTag];
                        int lastSlash = path.LastIndexOf('/');
                        string filename = lastSlash >= 0 ? path[(lastSlash + 1)..] : path;
                        sb.Append('[').Append(filename).Append(']');
                        i = closeTag + 6; // length of "[/img]"
                        continue;
                    }
                }

                int end = text.IndexOf(']', i);
                if (end >= 0) { i = end + 1; continue; }
            }
            sb.Append(text[i]);
            i++;
        }
        return sb.ToString();
    }

    internal static void SendJson(HttpListenerResponse response, object data)
    {
        string json = JsonSerializer.Serialize(data, _jsonOptions);
        byte[] buffer = Encoding.UTF8.GetBytes(json);
        response.ContentType = "application/json; charset=utf-8";
        response.ContentLength64 = buffer.Length;
        response.OutputStream.Write(buffer, 0, buffer.Length);
        response.Close();
    }

    internal static void SendText(HttpListenerResponse response, string text, string contentType = "text/plain")
    {
        byte[] buffer = Encoding.UTF8.GetBytes(text);
        response.ContentType = $"{contentType}; charset=utf-8";
        response.ContentLength64 = buffer.Length;
        response.OutputStream.Write(buffer, 0, buffer.Length);
        response.Close();
    }

    internal static void SendError(HttpListenerResponse response, int statusCode, string message)
    {
        response.StatusCode = statusCode;
        SendJson(response, new Dictionary<string, object?> { ["error"] = message });
    }

    private static Dictionary<string, object?> Error(string message)
    {
        return new Dictionary<string, object?> { ["status"] = "error", ["error"] = message };
    }

    /// <summary>
    /// Calls ForceClick() on a control while preserving the OS mouse cursor position.
    /// Godot's ForceClick() internally creates an InputEventMouseButton at (0,0),
    /// which warps the system cursor to the top-left corner of the screen.
    /// </summary>
    internal static void SafeForceClick(NClickableControl control)
    {
        var savedPos = DisplayServer.MouseGetPosition();
        control.ForceClick();
        DisplayServer.WarpMouse(savedPos);
    }

    internal static List<T> FindAll<T>(Node start) where T : Node
    {
        var list = new List<T>();
        if (GodotObject.IsInstanceValid(start))
            FindAllRecursive(start, list);
        return list;
    }

    /// <summary>
    /// FindAll variant that sorts results by visual position (row-major: top-to-bottom, left-to-right).
    /// NGridCardHolder.OnFocus() calls MoveToFront() which scrambles child order for z-rendering.
    /// Sorting by GlobalPosition restores the correct visual order for both single-row (card rewards,
    /// choose-a-card) and multi-row (deck selection grids) layouts.
    /// </summary>
    internal static List<T> FindAllSortedByPosition<T>(Node start) where T : Control
    {
        var list = FindAll<T>(start);
        list.Sort((a, b) =>
        {
            int cmp = a.GlobalPosition.Y.CompareTo(b.GlobalPosition.Y);
            return cmp != 0 ? cmp : a.GlobalPosition.X.CompareTo(b.GlobalPosition.X);
        });
        return list;
    }

    private static void FindAllRecursive<T>(Node node, List<T> found) where T : Node
    {
        if (!GodotObject.IsInstanceValid(node))
            return;
        if (node is T item)
            found.Add(item);
        foreach (var child in node.GetChildren())
            FindAllRecursive(child, found);
    }

    private static List<Dictionary<string, object?>> BuildHoverTips(IEnumerable<IHoverTip> tips)
    {
        var result = new List<Dictionary<string, object?>>();
        try
        {
            var seen = new HashSet<string>();
            foreach (var tip in IHoverTip.RemoveDupes(tips))
            {
                try
                {
                    string? title = null;
                    string? description = null;

                    if (tip is HoverTip ht)
                    {
                        title = ht.Title != null ? StripRichTextTags(ht.Title) : null;
                        description = StripRichTextTags(ht.Description);
                    }
                    else if (tip is CardHoverTip cardTip)
                    {
                        title = SafeGetText(() => cardTip.Card.Title);
                        description = SafeGetCardDescription(cardTip.Card);
                    }

                    if (title == null && description == null) continue;

                    string key = title ?? description!;
                    if (!seen.Add(key)) continue;

                    result.Add(new Dictionary<string, object?>
                    {
                        ["name"] = title,
                        ["description"] = description
                    });
                }
                catch { /* skip individual tip on error */ }
            }
        }
        catch { /* return partial results */ }
        return result;
    }

    internal static T? FindFirst<T>(Node start) where T : Node
    {
        if (!GodotObject.IsInstanceValid(start))
            return null;
        if (start is T result)
            return result;
        foreach (var child in start.GetChildren())
        {
            var val = FindFirst<T>(child);
            if (val != null) return val;
        }
        return null;
    }

    /// <summary>
    /// Parse enemy intent damage labels like "12", "3x4", "15 x2".
    /// Returns total damage (e.g., "3x4" → 12).
    /// </summary>
    internal static bool TryParseIntentDamage(string label, out int totalDamage)
    {
        totalDamage = 0;
        if (string.IsNullOrWhiteSpace(label)) return false;

        // Normalize: remove spaces around 'x' or '×'
        var normalized = label.Trim();

        // Try "NxM" or "N×M" or "N x M" patterns (multi-hit)
        foreach (char sep in new[] { 'x', '×', 'X' })
        {
            int sepIdx = normalized.IndexOf(sep);
            if (sepIdx > 0 && sepIdx < normalized.Length - 1)
            {
                var leftStr = normalized[..sepIdx].Trim();
                var rightStr = normalized[(sepIdx + 1)..].Trim();
                if (int.TryParse(leftStr, out int dmgPer) && int.TryParse(rightStr, out int hits))
                {
                    totalDamage = dmgPer * hits;
                    return true;
                }
            }
        }

        // Simple number
        if (int.TryParse(normalized, out int simple))
        {
            totalDamage = simple;
            return true;
        }

        return false;
    }
}
