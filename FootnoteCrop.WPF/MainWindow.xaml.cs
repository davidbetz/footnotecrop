using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Nalarium;
using Nalarium.Configuration;
using Path = System.IO.Path;

namespace FootnoteCrop.WPF
{
    public partial class MainWindow : Window, INotifyPropertyChanged
    {
        private ObservableCollection<string> _activeBookIndex;

        private ImageSource _activePageImageSource;

        private string _activeText;

        private bool? _isSaved;

        private string _leftPage;

        private ImageSource _leftPageImageSource;

        private string _fileType;

        private double _overlayHeight;

        private double _overlayLeft;

        private double _overlayTop;

        private double _overlayWidth;

        private string _page;

        private int _pageGridColumnCount;

        private string _rightPage;

        private ImageSource _rightPageImageSource;

        private double _savedOverlayTop;

        private double _savedOverlayWidth;

        private double _scaleX;

        private double _scaleY;

        public MainWindow()
        {
            InitializeComponent();

            Loaded += (sm, em) =>
            {
                DataContext = this;

                ActiveBookIndex = new ObservableCollection<string>();
                BasePath = ConfigAccessor.ApplicationSettings("BasePath");
                _fileType = ConfigAccessor.ApplicationSettings("fileType");
                if (string.IsNullOrEmpty(_fileType))
                {
                    _fileType = "jpg";
                }
                CoordinatePath = Path.Combine(BasePath, "CoordinateData");
                SourcePath = Path.Combine(BasePath, "VerticalCropped");
                if (!Directory.Exists(SourcePath))
                {
                    SourcePath = Path.Combine(BasePath, "TopCropped");
                }
                if (!Directory.Exists(SourcePath))
                {
                    SourcePath = Path.Combine(BasePath, "Straight");
                }
                if (!Directory.Exists(SourcePath))
                {
                    SourcePath = Path.Combine(BasePath, "Cropped");
                }

                if (ActiveBookIndex.Count == 0)
                {
                    foreach (var number in new DirectoryInfo(SourcePath)
                        .GetFiles($"*.{_fileType}")
                        .Where(p => p.Name.StartsWith("_"))
                        .Select(p => Parser.ParseInt32(p.Name.Substring(1, p.Name.Length - 5)))
                        .OrderBy(p => p)
                        .Select(p => p.ToString())
                        )
                    {
                        ActiveBookIndex.Add("_" + number);
                    }
                    foreach (var number in new DirectoryInfo(SourcePath)
                        .GetFiles($"*.{_fileType}")
                        .Where(p => !p.Name.StartsWith("_"))
                        .Select(p => Parser.ParseInt32(p.Name.Substring(0, p.Name.Length - 4)))
                        .OrderBy(p => p)
                        .Select(p => p.ToString())
                        )
                    {
                        ActiveBookIndex.Add(number);
                    }
                }

                MouseUp += (s, e) =>
                {
                    if (e.ChangedButton == MouseButton.Right)
                    {
                        var pageIndex = GetPageIndex(Page);
                        if (pageIndex == null)
                        {
                            return;
                        }
                        var index = (int)pageIndex + 1;
                        if (ActiveBookIndex.Count > index)
                        {
                            SetPage(ActiveBookIndex[index]);
                            PageListBox.SelectedItems.Clear();
                            PageListBox.SelectedItems.Add(PageListBox.Items.GetItemAt(index));
                        }
                    }
                };

                PreviewKeyUp += (s, e) =>
                {
                    var index = GetPageIndex(Page);
                    if (index == null)
                    {
                        return;
                    }
                    if (e.Key == Key.Left && index > 1)
                    {
                        SetPage(ActiveBookIndex[(int)index - 1]);
                    }
                    if (e.Key == Key.Right)
                    {
                        SetPage(ActiveBookIndex[(int)index + 1]);
                    }
                };

                ActiveCanvas.MouseUp += (s, e) =>
                {
                    if (e.ChangedButton == MouseButton.Left)
                    {
                        EndPoint = Mouse.GetPosition(ActiveCanvas);
                        Save();
                    }
                };
                ActiveCanvas.MouseMove += (s, e) =>
                {
                    OverlayTop = Current.Y;
                    Current = Mouse.GetPosition(ActiveCanvas);
                    var coordinateData = new StringBuilder();
                    if (StartPoint.Y != 0 && StartPoint.Y < Current.Y)
                    {
                        coordinateData.Append($"S({StartPoint.X},{StartPoint.Y}), ");
                    }
                    if (EndPoint.Y != 0 && e.LeftButton == MouseButtonState.Pressed)
                    {
                        coordinateData.Append($"E({EndPoint.X},{EndPoint.Y}); ");
                    }
                    coordinateData.Append($"({Current.X},{Current.Y})");
                    ActiveText = coordinateData.ToString();
                };

                PageListBox.SelectionChanged += (s, e) =>
                {
                    var pageList = e.Source as ListBox;
                    if (pageList?.SelectedItem == null)
                    {
                        return;
                    }
                    if (ActiveBookIndex == null)
                    {
                        return;
                    }
                    PageListBox.ScrollIntoView(PageListBox.SelectedItem);
                    SetPage((string)pageList.SelectedItem);
                };

                SetPage(ActiveBookIndex[0]);
            };
        }

        public string BasePath { get; set; }
        public string CoordinatePath { get; set; }
        public string SourcePath { get; set; }
        private Point Current { get; set; }
        private Point StartPoint { get; set; }
        private Point EndPoint { get; set; }

        public ObservableCollection<string> ActiveBookIndex
        {
            get { return _activeBookIndex; }
            set
            {
                _activeBookIndex = value;
                OnPropertyChanged("ActiveBookIndex");
            }
        }

        public string LeftPage
        {
            get { return _leftPage; }
            set
            {
                _leftPage = value;
                OnPropertyChanged("LeftPage");
            }
        }

        public string RightPage
        {
            get { return _rightPage; }
            set
            {
                _rightPage = value;
                OnPropertyChanged("RightPage");
            }
        }

        public string Page
        {
            get { return _page; }
            set
            {
                _page = value;
                OnPropertyChanged("Page");
            }
        }

        public bool? IsSaved
        {
            get { return _isSaved; }
            set
            {
                _isSaved = value;
                OnPropertyChanged("IsSaved");
            }
        }

        public ImageSource LeftPageImageSource
        {
            get { return _leftPageImageSource; }
            set
            {
                _leftPageImageSource = value;
                OnPropertyChanged("LeftPageImageSource");
            }
        }

        public ImageSource RightPageImageSource
        {
            get { return _rightPageImageSource; }
            set
            {
                _rightPageImageSource = value;
                OnPropertyChanged("RightPageImageSource");
            }
        }

        public ImageSource ActivePageImageSource
        {
            get { return _activePageImageSource; }
            set
            {
                _activePageImageSource = value;
                OnPropertyChanged("ActivePageImageSource");
            }
        }

        public string ActiveText
        {
            get { return _activeText; }
            set
            {
                _activeText = value;
                OnPropertyChanged("ActiveText");
            }
        }

        public double SavedOverlayWidth
        {
            get { return _savedOverlayWidth; }
            set
            {
                _savedOverlayWidth = value;
                OnPropertyChanged("SavedOverlayWidth");
            }
        }

        public double SavedOverlayTop
        {
            get { return _savedOverlayTop; }
            set
            {
                _savedOverlayTop = value;
                OnPropertyChanged("SavedOverlayTop");
            }
        }

        public double OverlayWidth
        {
            get { return _overlayWidth; }
            set
            {
                _overlayWidth = value;
                OnPropertyChanged("OverlayWidth");
            }
        }

        public double OverlayHeight
        {
            get { return _overlayHeight; }
            set
            {
                _overlayHeight = value;
                OnPropertyChanged("OverlayHeight");
            }
        }

        public double OverlayTop
        {
            get { return _overlayTop; }
            set
            {
                _overlayTop = value;
                OnPropertyChanged("OverlayTop");
            }
        }

        public double OverlayLeft
        {
            get { return _overlayLeft; }
            set
            {
                _overlayLeft = value;
                OnPropertyChanged("OverlayLeft");
            }
        }

        public int PageGridColumnCount
        {
            get { return _pageGridColumnCount; }
            set
            {
                _pageGridColumnCount = value;
                OnPropertyChanged("PageGridColumnCount");
            }
        }

        public double ScaleX
        {
            get { return _scaleX; }
            set
            {
                _scaleX = value;
                OnPropertyChanged("ScaleX");
            }
        }

        public double ScaleY
        {
            get { return _scaleY; }
            set
            {
                _scaleY = value;
                OnPropertyChanged("ScaleY");
            }
        }

        public event PropertyChangedEventHandler PropertyChanged = delegate { };

        private void SetPage(string page)
        {
            OverlayTop = 0;
            OverlayWidth = ActiveCanvas.ActualWidth;
            OverlayHeight = 4;
            OverlayLeft = 0;

            IsSaved = false;

            LoadPage(page);
        }

        private void LoadPage(string page)
        {
            if (ActiveBookIndex == null)
            {
                return;
            }

            var pageIndex = GetPageIndex(page);

            var leftPageIndex = pageIndex - 1;
            if (pageIndex == 0)
            {
                leftPageIndex = null;
            }

            var rightPageIndex = pageIndex + 1;
            if (pageIndex + 1 == ActiveBookIndex.Count)
            {
                rightPageIndex = null;
            }

            SetImageSource(leftPageIndex, b => { LeftPageImageSource = b; });
            SetImageSource(rightPageIndex, b => { RightPageImageSource = b; });
            SetImageSource(pageIndex, b => { ActivePageImageSource = b; });

            if (leftPageIndex != null)
            {
                LeftPage = ActiveBookIndex[(int)leftPageIndex];
            }
            Page = page;
            if (rightPageIndex != null)
            {
                if (ActiveBookIndex.Count > rightPageIndex)
                {
                    RightPage = ActiveBookIndex[(int)rightPageIndex];
                }
            }
        }

        private void SetImageSource(int? index, Action<ImageSource> setter)
        {
            try
            {
                if (index != null)
                {
                    var indexInt32 = (int)index;
                    var filename = Path.Combine(SourcePath, ActiveBookIndex[indexInt32] + "." + _fileType);
                    if (File.Exists(filename))
                    {
                        setter(new BitmapImage(new Uri(filename, UriKind.Relative)));
                    }
                }
                else
                {
                    setter(null);
                }
            }
            catch (Exception ex)
            {
                ActiveText = ex.Message;
            }
        }

        public void Save()
        {
            var bitmapImage = ActivePageImageSource as BitmapImage;
            if (bitmapImage == null)
            {
                return;
            }
            var output = (bitmapImage.PixelHeight / ActiveCanvas.RenderSize.Height * OverlayTop).ToString(CultureInfo.InvariantCulture);
            if (!Directory.Exists(CoordinatePath))
            {
                Directory.CreateDirectory(CoordinatePath);
            }
            File.WriteAllText(Path.Combine(CoordinatePath, Page + ".txt"), output);
            SavedOverlayTop = OverlayTop;
            SavedOverlayWidth = OverlayWidth;
            IsSaved = true;
        }

        private int? GetPageIndex(string page)
        {
            return ActiveBookIndex.ToList().IndexOf(page);
        }

        private void OnPropertyChanged(string propertyName)
        {
            PropertyChanged(this, new PropertyChangedEventArgs(propertyName));
        }
    }
}