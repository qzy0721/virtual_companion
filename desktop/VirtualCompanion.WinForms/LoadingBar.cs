using System.Drawing.Drawing2D;

namespace VirtualCompanion.Desktop;

/// <summary>
/// 主题色 Marquee 加载条：浅色圆角轨道 + 粉紫渐变滑动块
/// （颜色与前端主题 #7c8cff / #ff7fa3 呼应）。
/// </summary>
internal sealed class LoadingBar : Control
{
    private const float BlockWidth = 130f;

    private static readonly Color TrackLight = Color.FromArgb(46, 124, 140, 255);
    private static readonly Color TrackDark = Color.FromArgb(46, 143, 157, 255);
    private static readonly Color GradientFromLight = Color.FromArgb(124, 140, 255); // #7c8cff
    private static readonly Color GradientToLight = Color.FromArgb(255, 127, 163);  // #ff7fa3
    private static readonly Color GradientFromDark = Color.FromArgb(143, 157, 255); // #8f9dff
    private static readonly Color GradientToDark = Color.FromArgb(255, 143, 176);   // #ff8fb0

    private readonly System.Windows.Forms.Timer _timer;
    private float _offset;
    private Color _trackColor = TrackLight;
    private Color _gradientFrom = GradientFromLight;
    private Color _gradientTo = GradientToLight;

    public LoadingBar()
    {
        // 不能用 OptimizedDoubleBuffer：双缓冲经 GDI BitBlt 合成，alpha 丢失
        // （半透明轨道会变实色、圆角外变黑）。区域小，单缓冲绘制不会闪烁。
        SetStyle(ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
        _timer = new System.Windows.Forms.Timer { Interval = 30 };
        _timer.Tick += (_, _) =>
        {
            _offset += 4f;
            if (_offset > Width + BlockWidth)
            {
                _offset = -BlockWidth;
            }
            Invalidate();
        };
    }

    // 加载条配色跟随系统深浅（与前端星夜蓝黑主题同源）。
    public void ApplyDark(bool dark)
    {
        _trackColor = dark ? TrackDark : TrackLight;
        _gradientFrom = dark ? GradientFromDark : GradientFromLight;
        _gradientTo = dark ? GradientToDark : GradientToLight;
        Invalidate();
    }

    public bool Running
    {
        get => _timer.Enabled;
        set
        {
            _timer.Enabled = value;
            if (!value)
            {
                Invalidate();
            }
        }
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.Clear(BackColor);
        g.SmoothingMode = SmoothingMode.AntiAlias;

        var radius = Height / 2f;
        var track = new RectangleF(0, 0, Width, Height);
        using (var trackPath = RoundedRect(track, radius))
        using (var trackBrush = new SolidBrush(_trackColor))
        {
            g.FillPath(trackBrush, trackPath);
        }

        var block = new RectangleF(_offset, 1f, BlockWidth, Math.Max(Height - 2f, 1f));
        using (var gradient = new LinearGradientBrush(block, _gradientFrom, _gradientTo, LinearGradientMode.Horizontal))
        using (var blockPath = RoundedRect(block, Math.Max(block.Height / 2f, 1f)))
        {
            g.FillPath(gradient, blockPath);
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _timer.Dispose();
        }
        base.Dispose(disposing);
    }

    private static GraphicsPath RoundedRect(RectangleF bounds, float radius)
    {
        var path = new GraphicsPath();
        if (radius <= 0)
        {
            path.AddRectangle(bounds);
            return path;
        }

        var d = radius * 2f;
        path.AddArc(bounds.X, bounds.Y, d, d, 180, 90);
        path.AddArc(bounds.Right - d, bounds.Y, d, d, 270, 90);
        path.AddArc(bounds.Right - d, bounds.Bottom - d, d, d, 0, 90);
        path.AddArc(bounds.X, bounds.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }
}
