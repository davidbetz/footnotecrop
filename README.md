# Footnote Crop

**Copyright 2013-2016 David Betz**

This is a highly specialized tool used to aide in cropping footnotes off scanned pages to prepare for OCR.

## Usage

You state the base path and the file type.

    <appSettings>
      <add key="BasePath" value="C:\_BOOK\ScannedBook" />
      <add key="FileType" value="png" />
    </appSettings>
    
The tool will look for one of the following folders under the base path in the following order:

* ./VerticalCropped
* ./TopCropped
* ./Straight
* ./Cropped

You don't have to know what the terms mean. They are part of my proprietary e-book creation procedure.

In the application, you use left-click to mark a page for cutting and right-click to go to next page. This means that processing a 1000 page book can be processed fairly quickly.

## Post-processing

This application does not cut the files. I believe in having an army of smaller tools, eaching doing what they do well. The actual cropping is done with ImageMagick. You need to have it installed. This application creates text files with coordinates, one per page. These files are in the `./CoordinateData` folder.

The following PowerShell code will look for image folder using the same pattern as before, then it will use the coordinate data with [ImageMagick](http://www.imagemagick.org/script/index.php) to create cropped images. The final images will be in `./FullCropped`

    $scope = {
        param(
            [string] $book
        )
        $base = 'C:\_BOOK'
        $folder = Join-Path $base $book

        $cropFolder = Join-Path $folder 'VerticalCropped'
        if(!(Test-Path $cropFolder)) {
            $cropFolder = Join-Path $folder 'TopCropped'
        }
        if(!(Test-Path $cropFolder)) {
            $cropFolder = Join-Path $folder 'Straight'
        }
        if(!(Test-Path $cropFolder)) {
            $cropFolder = Join-Path $folder 'Cropped'
        }
        $dataFolder = Join-Path $folder 'CoordinateData'
        $targetFolder = Join-Path $folder 'FullCropped'

        if(!(Test-Path $targetFolder)) {
            mkdir $targetFolder | Out-Null
        }
        Write-Host $cropFolder
        (([array](ls $cropFolder))).ForEach({
            $sourceFile = $_.FullName
            $name = $_.Name.substring(0, $_.Name.Length - 4)

                $dataFile = Join-Path $dataFolder ($name + ".txt")
                if(!(Test-Path $dataFile)) {
                    cp $sourceFile $targetFolder
                }
                else {
                    $coordinateData = [int](cat $dataFile)

                    Write-Host "$name`:$coordinateData"

                    &"C:\Program Files\ImageMagick-6.9.2-Q16\convert.exe" $sourceFile -crop "0x$coordinateData+0+0" "$targetFolder\$name.png"
                }
          #  }
        })
    }
    &$scope -book 'ScannedBook'