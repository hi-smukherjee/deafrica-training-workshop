## deafrica_spatialtools.py
'''
Description: This file contains a set of python functions for conducting 
spatial analyses on Digital Earth Africa data.

License: The code in this notebook is licensed under the Apache License, 
Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0). Digital Earth 
Africa data is licensed under the Creative Commons by Attribution 4.0 
license (https://creativecommons.org/licenses/by/4.0/).

Contact: If you need assistance, please post a question on the Open Data 
Cube Slack channel (http://slack.opendatacube.org/) or on the GIS Stack 
Exchange (https://gis.stackexchange.com/questions/ask?tags=open-data-cube) 
using the `open-data-cube` tag (you can view previously asked questions 
here: https://gis.stackexchange.com/questions/tagged/open-data-cube). 

If you would like to report an issue with this script, file one on 
Github: https://github.com/GeoscienceAustralia/dea-notebooks/issues/new

Functions included:
    xr_vectorize
    xr_rasterize
    subpixel_contours
    interpolate_2d
    contours_to_array
    largest_region
    transform_geojson_wgs_to_epsg

Last modified: February 2020

'''

# Import required packages
import osr
import ogr
import collections
import numpy as np
import xarray as xr
import geopandas as gpd
import rasterio.features
import scipy.interpolate
from scipy import ndimage as nd
from skimage.measure import label
from skimage.measure import find_contours
from shapely.geometry import LineString, MultiLineString, shape
from datacube.helpers import write_geotiff
from datacube.utils.geometry import assign_crs

def xr_vectorize(da, 
                 attribute_col='attribute', 
                 transform=None, 
                 crs=None, 
                 dtype='float32',
                 export_shp=False,
                 **rasterio_kwargs):    
    """
    Vectorises a xarray.DataArray into a geopandas.GeoDataFrame.
    
    Parameters
    ----------
    da : xarray dataarray or a numpy ndarray  
    attribute_col : str, optional
        Name of the attribute column in the resulting geodataframe. 
        Values of the raster object converted to polygons will be 
        assigned to this column. Defaults to 'attribute'.
    transform : affine.Affine object, optional
        An affine.Affine object (e.g. `from affine import Affine; 
        Affine(30.0, 0.0, 548040.0, 0.0, -30.0, "6886890.0) giving the 
        affine transformation used to convert raster coordinates 
        (e.g. [0, 0]) to geographic coordinates. If none is provided, 
        the function will attempt to obtain an affine transformation 
        from the xarray object (e.g. either at `da.transform` or
        `da.geobox.transform`).
    crs : str or CRS object, optional
        An EPSG string giving the coordinate system of the array 
        (e.g. 'EPSG:3577'). If none is provided, the function will 
        attempt to extract a CRS from the xarray object's `crs` 
        attribute.
    dtype : str, optional
         Data type must be one of int16, int32, uint8, uint16, 
         or float32
    export_shp : Boolean or string path, optional
        To export the output vectorised features to a shapefile, supply
        an output path (e.g. 'output_dir/output.shp'. The default is 
        False, which will not write out a shapefile. 
    **rasterio_kwargs : 
        A set of keyword arguments to rasterio.features.shapes
        Can include `mask` and `connectivity`.
    
    Returns
    -------
    gdf : Geopandas GeoDataFrame
    
    """
    try:
        crs = da.geobox.crs
    except:
        try:
            crs = da.crs
        except:
            if crs is None:
                raise Exception("Please add a `crs` attribute to the "
                            "xarray.DataArray, or provide a CRS using the "
                            "function's `crs` parameter (e.g. 'EPSG:6933')")
            
    # Check if transform is provided as a xarray.DataArray method.
    # If not, require supplied Affine
    if transform is None:
        try:
            # First, try to take transform info from geobox
            transform = da.geobox.transform
        # If no geobox
        except:
            try:
                # Try getting transform from 'transform' attribute
                transform = da.transform
            except:
                # If neither of those options work, raise an exception telling the 
                # user to provide a transform
                raise Exception("Please provide an Affine transform object using the "
                        "`transform` parameter (e.g. `from affine import "
                        "Affine; Affine(30.0, 0.0, 548040.0, 0.0, -30.0, "
                        "6886890.0)`")
    
    # Check to see if the input is a numpy array
    if type(da) is np.ndarray:
        vectors = rasterio.features.shapes(source=da.astype(dtype),
                                           transform=transform,
                                           **rasterio_kwargs)
    
    else:
        # Run the vectorizing function
        vectors = rasterio.features.shapes(source=da.data.astype(dtype),
                                           transform=transform,
                                           **rasterio_kwargs)
    
    # Convert the generator into a list
    vectors = list(vectors)
    
    # Extract the polygon coordinates and values from the list
    polygons = [polygon for polygon, value in vectors]
    values = [value for polygon, value in vectors]
    
    # Convert polygon coordinates into polygon shapes
    polygons = [shape(polygon) for polygon in polygons]
    
    # Create a geopandas dataframe populated with the polygon shapes
    gdf = gpd.GeoDataFrame(data={attribute_col: values},
                           geometry=polygons,
                           crs=crs)
    
    # If a file path is supplied, export a shapefile
    if export_shp:
        gdf.to_file(export_shp) 
        
    return gdf


def xr_rasterize(gdf,
                 da,
                 attribute_col=False,
                 crs=None,
                 transform=None,
                 name=None,
                 x_dim='x',
                 y_dim='y',
                 export_tiff=None,
                 **rasterio_kwargs):    
    """
    Rasterizes a geopandas.GeoDataFrame into an xarray.DataArray.
    
    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        A geopandas.GeoDataFrame object containing the vector/shapefile
        data you want to rasterise.
    da : xarray.DataArray or xarray.Dataset
        The shape, coordinates, dimensions, and transform of this object 
        are used to build the rasterized shapefile. It effectively 
        provides a template. The attributes of this object are also 
        appended to the output xarray.DataArray.
    attribute_col : string, optional
        Name of the attribute column in the geodataframe that the pixels 
        in the raster will contain.  If set to False, output will be a 
        boolean array of 1's and 0's.
    crs : str, optional
        CRS metadata to add to the output xarray. e.g. 'epsg:3577'.
        The function will attempt get this info from the input 
        GeoDataFrame first.
    transform : affine.Affine object, optional
        An affine.Affine object (e.g. `from affine import Affine; 
        Affine(30.0, 0.0, 548040.0, 0.0, -30.0, "6886890.0) giving the 
        affine transformation used to convert raster coordinates 
        (e.g. [0, 0]) to geographic coordinates. If none is provided, 
        the function will attempt to obtain an affine transformation 
        from the xarray object (e.g. either at `da.transform` or
        `da.geobox.transform`).
    x_dim : str, optional
        An optional string allowing you to override the xarray dimension 
        used for x coordinates. Defaults to 'x'. Useful, for example, 
        if x and y dims instead called 'lat' and 'lon'.   
    y_dim : str, optional
        An optional string allowing you to override the xarray dimension 
        used for y coordinates. Defaults to 'y'. Useful, for example, 
        if x and y dims instead called 'lat' and 'lon'.
    export_tiff: str, optional
        If a filepath is provided (e.g 'output/output.tif'), will export a
        geotiff file. A named array is required for this operation, if one
        is not supplied by the user a default name, 'data', is used
    **rasterio_kwargs : 
        A set of keyword arguments to rasterio.features.rasterize
        Can include: 'all_touched', 'merge_alg', 'dtype'.
    
    Returns
    -------
    xarr : xarray.DataArray
    
    """
    
    # Check for a crs object
    try:
        crs = da.geobox.crs
    except:
        try:
            crs = da.crs
        except:
            if crs is None:
                raise Exception("Please add a `crs` attribute to the "
                            "xarray.DataArray, or provide a CRS using the "
                            "function's `crs` parameter (e.g. crs='EPSG:3577')")
    
    # Check if transform is provided as a xarray.DataArray method.
    # If not, require supplied Affine
    if transform is None:
        try:
            # First, try to take transform info from geobox
            transform = da.geobox.transform
        # If no geobox
        except:
            try:
                # Try getting transform from 'transform' attribute
                transform = da.transform
            except:
                # If neither of those options work, raise an exception telling the 
                # user to provide a transform
                raise Exception("Please provide an Affine transform object using the "
                        "`transform` parameter (e.g. `from affine import "
                        "Affine; Affine(30.0, 0.0, 548040.0, 0.0, -30.0, "
                        "6886890.0)`")
    
    # Grab the 2D dims (not time)    
    try:
        dims = da.geobox.dims
    except:
        dims = y_dim, x_dim  
    
    # Coords
    xy_coords = [da[dims[0]], da[dims[1]]]
    
    # Shape
    try:
        y, x = da.geobox.shape
    except:
        y, x = len(xy_coords[0]), len(xy_coords[1])
    
    # Reproject shapefile to match CRS of raster
    print(f'Rasterizing to match xarray.DataArray dimensions ({y}, {x})')
    
    try:
        gdf_reproj = gdf.to_crs(crs=crs)
    except:
        # Sometimes the crs can be a datacube utils CRS object
        # so convert to string before reprojecting
        gdf_reproj = gdf.to_crs(crs={'init': str(crs)})
    
    # If an attribute column is specified, rasterise using vector 
    # attribute values. Otherwise, rasterise into a boolean array
    if attribute_col:        
        # Use the geometry and attributes from `gdf` to create an iterable
        shapes = zip(gdf_reproj.geometry, gdf_reproj[attribute_col])
    else:
        # Use geometry directly (will produce a boolean numpy array)
        shapes = gdf_reproj.geometry

    # Rasterise shapes into an array
    arr = rasterio.features.rasterize(shapes=shapes,
                                      out_shape=(y, x),
                                      transform=transform,
                                      **rasterio_kwargs)
        
    # Convert result to a xarray.DataArray
    xarr = xr.DataArray(arr,
                        coords=xy_coords,
                        dims=dims,
                        attrs=da.attrs,
                        name=name if name else None)
    
    # Add back crs if xarr.attrs doesn't have it
    if xarr.geobox is None:
        xarr = assign_crs(xarr, str(crs))
    
    if export_tiff:        
        print(f"Exporting GeoTIFF to {export_tiff}")
        write_cog(ds,
                  export_tiff,
                  overwrite=True)
                
    return xarr


def subpixel_contours(da,
                      z_values=[0.0],
                      crs=None,
                      affine=None,
                      attribute_df=None,
                      output_path=None,
                      min_vertices=2,
                      dim='time'):
    
    """
    Uses `skimage.measure.find_contours` to extract multiple z-value 
    contour lines from a two-dimensional array (e.g. multiple elevations
    from a single DEM), or one z-value for each array along a specified 
    dimension of a multi-dimensional array (e.g. to map waterlines 
    across time by extracting a 0 NDWI contour from each individual 
    timestep in an xarray timeseries).    
    
    Contours are returned as a geopandas.GeoDataFrame with one row per 
    z-value or one row per array along a specified dimension. The 
    `attribute_df` parameter can be used to pass custom attributes 
    to the output contour features.
    
    Last modified: April 2020
    
    Parameters
    ----------  
    da : xarray DataArray
        A two-dimensional or multi-dimensional array from which 
        contours are extracted. If a two-dimensional array is provided, 
        the analysis will run in 'single array, multiple z-values' mode 
        which allows you to specify multiple `z_values` to be extracted.
        If a multi-dimensional array is provided, the analysis will run 
        in 'single z-value, multiple arrays' mode allowing you to 
        extract contours for each array along the dimension specified 
        by the `dim` parameter.  
    z_values : int, float or list of ints, floats
        An individual z-value or list of multiple z-values to extract 
        from the array. If operating in 'single z-value, multiple 
        arrays' mode specify only a single z-value.
    crs : string or CRS object, optional
        An EPSG string giving the coordinate system of the array 
        (e.g. 'EPSG:3577'). If none is provided, the function will 
        attempt to extract a CRS from the xarray object's `crs` 
        attribute.
    affine : affine.Affine object, optional
        An affine.Affine object (e.g. `from affine import Affine; 
        Affine(30.0, 0.0, 548040.0, 0.0, -30.0, "6886890.0) giving the 
        affine transformation used to convert raster coordinates 
        (e.g. [0, 0]) to geographic coordinates. If none is provided, 
        the function will attempt to obtain an affine transformation 
        from the xarray object (e.g. either at `da.transform` or
        `da.geobox.transform`).
    output_path : string, optional
        The path and filename for the output shapefile.
    attribute_df : pandas.Dataframe, optional
        A pandas.Dataframe containing attributes to pass to the output
        contour features. The dataframe must contain either the same 
        number of rows as supplied `z_values` (in 'multiple z-value, 
        single array' mode), or the same number of rows as the number 
        of arrays along the `dim` dimension ('single z-value, multiple 
        arrays mode').
    min_vertices : int, optional
        The minimum number of vertices required for a contour to be 
        extracted. The default (and minimum) value is 2, which is the 
        smallest number required to produce a contour line (i.e. a start
        and end point). Higher values remove smaller contours, 
        potentially removing noise from the output dataset.
    dim : string, optional
        The name of the dimension along which to extract contours when 
        operating in 'single z-value, multiple arrays' mode. The default
        is 'time', which extracts contours for each array along the time
        dimension.
        
    Returns
    -------
    output_gdf : geopandas geodataframe
        A geopandas geodataframe object with one feature per z-value 
        ('single array, multiple z-values' mode), or one row per array 
        along the dimension specified by the `dim` parameter ('single 
        z-value, multiple arrays' mode). If `attribute_df` was 
        provided, these values will be included in the shapefile's 
        attribute table.
    
    Notes
    -----
    For more information about this function and how it can be applied
    to satellite data, refer to:
    
        https://github.com/GeoscienceAustralia/dea-notebooks/tree/subpixel_waterlines
    
    To cite this work:
    
        Bishop-Taylor, R., Sagar, S., Lymburner, L., Alam, I. and 
        Sixsmith, J., 2019. Sub-Pixel Waterline Extraction: 
        Characterising Accuracy and Sensitivity to Indices and Spectra. 
        Remote Sensing, 11(24), p.2984. Available: 
        https://doi.org/10.3390/rs11242984
        
    """

    def contours_to_multiline(da_i, z_value, min_vertices=2):
        '''
        Helper function to apply marching squares contour extraction
        to an array and return a data as a shapely MultiLineString.
        The `min_vertices` parameter allows you to drop small contours 
        with less than X vertices.
        '''
        
        # Extracts contours from array, and converts each discrete
        # contour into a Shapely LineString feature
        line_features = [LineString(i[:,[1, 0]]) 
                         for i in find_contours(da_i.data, z_value) 
                         if i.shape[0] > min_vertices]

        # Output resulting lines into a single combined MultiLineString
        return MultiLineString(line_features)

    # Check if CRS is provided as a xarray.DataArray attribute.
    # If not, require supplied CRS
    try:
        crs = da.crs
    except:
        if crs is None:
            raise Exception("Please add a `crs` attribute to the "
                            "xarray.DataArray, or provide a CRS using the "
                            "function's `crs` parameter (e.g. 'EPSG:3577')")

    # Check if Affine transform is provided as a xarray.DataArray method.
    # If not, require supplied Affine
    try:
        affine = da.geobox.transform
    except KeyError:
        affine = da.transform
    except:
        if affine is None:
            raise Exception("Please provide an Affine object using the "
                            "`affine` parameter (e.g. `from affine import "
                            "Affine; Affine(30.0, 0.0, 548040.0, 0.0, -30.0, "
                            "6886890.0)`")

    # If z_values is supplied is not a list, convert to list:
    z_values = z_values if (isinstance(z_values, list) or 
                            isinstance(z_values, np.ndarray)) else [z_values]

    # Test number of dimensions in supplied data array
    if len(da.shape) == 2:

        print(f'Operating in multiple z-value, single array mode')
        dim = 'z_value'
        contour_arrays = {str(i)[0:10]: 
                          contours_to_multiline(da, i, min_vertices) 
                          for i in z_values}    

    else:

        # Test if only a single z-value is given when operating in 
        # single z-value, multiple arrays mode
        print(f'Operating in single z-value, multiple arrays mode')
        if len(z_values) > 1:
            raise Exception('Please provide a single z-value when operating '
                            'in single z-value, multiple arrays mode')

        contour_arrays = {str(i)[0:10]: 
                          contours_to_multiline(da_i, z_values[0], min_vertices) 
                          for i, da_i in da.groupby(dim)}

    # If attributes are provided, add the contour keys to that dataframe
    if attribute_df is not None:

        try:
            attribute_df.insert(0, dim, contour_arrays.keys())
        except ValueError:

            raise Exception("One of the following issues occured:\n\n"
                            "1) `attribute_df` contains a different number of "
                            "rows than the number of supplied `z_values` ("
                            "'multiple z-value, single array mode')\n"
                            "2) `attribute_df` contains a different number of "
                            "rows than the number of arrays along the `dim` "
                            "dimension ('single z-value, multiple arrays mode')")

    # Otherwise, use the contour keys as the only main attributes
    else:
        attribute_df = list(contour_arrays.keys())

    # Convert output contours to a geopandas.GeoDataFrame
    contours_gdf = gpd.GeoDataFrame(data=attribute_df, 
                                    geometry=list(contour_arrays.values()),
                                    crs=crs)   

    # Define affine and use to convert array coords to geographic coords.
    # We need to add 0.5 x pixel size to the x and y to obtain the centre 
    # point of our pixels, rather than the top-left corner
    shapely_affine = [affine.a, affine.b, affine.d, affine.e, 
                      affine.xoff + affine.a / 2.0, 
                      affine.yoff + affine.e / 2.0]
    contours_gdf['geometry'] = contours_gdf.affine_transform(shapely_affine)

    # Rename the data column to match the dimension
    contours_gdf = contours_gdf.rename({0: dim}, axis=1)

    # Drop empty timesteps
    empty_contours = contours_gdf.geometry.is_empty
    failed = ', '.join(map(str, contours_gdf[empty_contours][dim].to_list()))
    contours_gdf = contours_gdf[~empty_contours]
    if empty_contours.any():  
        print(f'Failed to generate contours: {failed}')

    # If asked to write out file, test if geojson or shapefile
    if output_path and output_path.endswith('.geojson'):
        print(f'Writing contours to {output_path}')
        contours_gdf.to_crs({'init': 'EPSG:4326'}).to_file(filename=output_path, 
                                                           driver='GeoJSON')
    if output_path and output_path.endswith('.shp'):
        print(f'Writing contours to {output_path}')
        contours_gdf.to_file(filename=output_path)
        
    return contours_gdf


def interpolate_2d(ds, 
                   x_coords, 
                   y_coords, 
                   z_coords, 
                   method='linear',
                   factor=1,
                   **kwargs):
    
    """
    This function takes points with X, Y and Z coordinates, and 
    interpolates Z-values across the extent of an existing xarray 
    dataset. This can be useful for producing smooth surfaces from point
    data that can be compared directly against satellite data derived 
    from an OpenDataCube query.
    
    Supported interpolation methods include 'linear', 'nearest' and
    'cubic (using `scipy.interpolate.griddata`), and 'rbf' (using 
    `scipy.interpolate.Rbf`).
    
    Last modified: February 2020
    
    Parameters
    ----------  
    ds : xarray DataArray or Dataset
        A two-dimensional or multi-dimensional array from which x and y 
        dimensions will be copied and used for the area in which to 
        interpolate point data. 
    x_coords, y_coords : numpy array
        Arrays containing X and Y coordinates for all points (e.g. 
        longitudes and latitudes).
    z_coords : numpy array
        An array containing Z coordinates for all points (e.g. 
        elevations). These are the values you wish to interpolate 
        between.
    method : string, optional
        The method used to interpolate between point values. This string
        is either passed to `scipy.interpolate.griddata` (for 'linear', 
        'nearest' and 'cubic' methods), or used to specify Radial Basis 
        Function interpolation using `scipy.interpolate.Rbf` ('rbf').
        Defaults to 'linear'.
    factor : int, optional
        An optional integer that can be used to subsample the spatial 
        interpolation extent to obtain faster interpolation times, then
        up-sample this array back to the original dimensions of the 
        data as a final step. For example, setting `factor=10` will 
        interpolate data into a grid that has one tenth of the 
        resolution of `ds`. This approach will be significantly faster 
        than interpolating at full resolution, but will potentially 
        produce less accurate or reliable results.
    **kwargs : 
        Optional keyword arguments to pass to either 
        `scipy.interpolate.griddata` (if `method` is 'linear', 'nearest' 
        or 'cubic'), or `scipy.interpolate.Rbf` (is `method` is 'rbf').
      
    Returns
    -------
    interp_2d_array : xarray DataArray
        An xarray DataArray containing with x and y coordinates copied 
        from `ds_array`, and Z-values interpolated from the points data. 
    """
    
    # Extract xy and elev points
    points_xy = np.vstack([x_coords, y_coords]).T
    
    # Extract x and y coordinates to interpolate into. 
    # If `factor` is greater than 1, the coordinates will be subsampled 
    # for faster run-times. If the last x or y value in the subsampled 
    # grid aren't the same as the last x or y values in the original 
    # full resolution grid, add the final full resolution grid value to 
    # ensure data is interpolated up to the very edge of the array
    if ds.x[::factor][-1].item() == ds.x[-1].item():
        x_grid_coords = ds.x[::factor].values
    else:
        x_grid_coords = ds.x[::factor].values.tolist() + [ds.x[-1].item()]
        
    if ds.y[::factor][-1].item() == ds.y[-1].item():
        y_grid_coords = ds.y[::factor].values
    else:
        y_grid_coords = ds.y[::factor].values.tolist() + [ds.y[-1].item()]

    # Create grid to interpolate into
    grid_y, grid_x = np.meshgrid(x_grid_coords, y_grid_coords)
    
    # Apply scipy.interpolate.griddata interpolation methods
    if method in ('linear', 'nearest', 'cubic'):
        
        # Interpolate x, y and z values 
        interp_2d = scipy.interpolate.griddata(points=points_xy, 
                                               values=z_coords, 
                                               xi=(grid_y, grid_x), 
                                               method=method,
                                               **kwargs)
    
    # Apply Radial Basis Function interpolation
    elif method == 'rbf':

        # Interpolate x, y and z values 
        rbf = scipy.interpolate.Rbf(x_coords, y_coords, z_coords, **kwargs)  
        interp_2d = rbf(grid_y, grid_x)

    # Create xarray dataarray from the data and resample to ds coords
    interp_2d_da = xr.DataArray(interp_2d, 
                                coords=[y_grid_coords, x_grid_coords], 
                                dims=['y', 'x'])
    
    # If factor is greater than 1, resample the interpolated array to
    # match the input `ds` array
    if factor > 1: 
        interp_2d_da = interp_2d_da.interp_like(ds)   

    return interp_2d_da


def contours_to_arrays(gdf, col):
    
    """
    This function converts a polyline shapefile into an array with three
    columns giving the X, Y and Z coordinates of each vertex. This data
    can then be used as an input to interpolation procedures (e.g. using 
    a function like `interpolate_2d`.
    
    Last modified: October 2019
    
    Parameters
    ----------  
    gdf : Geopandas GeoDataFrame
        A GeoPandas GeoDataFrame of lines to convert into point 
        coordinates.
    col : str
        A string giving the name of the GeoDataFrame field to use as 
        Z-values.
        
    Returns
    -------
    A numpy array with three columns giving the X, Y and Z coordinates 
    of each vertex in the input GeoDataFrame.
        
    """        

    coords_zvals = []

    for i in range(0, len(gdf)):

        val = gdf.iloc[i][col]

        try:
            coords = np.concatenate([np.vstack(x.coords.xy).T 
                                     for x in gdf.iloc[i].geometry])
        except:
            coords = np.vstack(gdf.iloc[i].geometry.coords.xy).T

        coords_zvals.append(np.column_stack((coords, 
                                             np.full(np.shape(coords)[0], 
                                                     fill_value=val))))

    return np.concatenate(coords_zvals)


def largest_region(bool_array, **kwargs):
    
    '''
    Takes a boolean array and identifies the largest contiguous region of 
    connected True values. This is returned as a new array with cells in 
    the largest region marked as True, and all other cells marked as False.
    
    Parameters
    ----------  
    bool_array : boolean array
        A boolean array (numpy or xarray.DataArray) with True values for
        the areas that will be inspected to find the largest group of 
        connected cells
    **kwargs : 
        Optional keyword arguments to pass to `measure.label`
        
    Returns
    -------
    largest_region : boolean array
        A boolean array with cells in the largest region marked as True, 
        and all other cells marked as False.       
        
    '''
    
    # First, break boolean array into unique, discrete regions/blobs
    blobs_labels = label(bool_array, background=0, **kwargs)
    
    # Count the size of each blob, excluding the background class (0)
    ids, counts = np.unique(blobs_labels[blobs_labels > 0], 
                            return_counts=True) 
    
    # Identify the region ID of the largest blob
    largest_region_id = ids[np.argmax(counts)]
    
    # Produce a boolean array where 1 == the largest region
    largest_region = blobs_labels == largest_region_id
    
    return largest_region


def transform_geojson_wgs_to_epsg(geojson, EPSG):
    
    """
    Takes a geojson dictionary and converts it from WGS84 (EPSG:4326) to desired EPSG
    
    Parameters
    ----------
    geojson: dict
        a geojson dictionary containing a 'geometry' key, in WGS84 coordinates
    EPSG: int
        numeric code for the EPSG coordinate referecnce system to transform into
        
    Returns
    -------
    transformed_geojson: dict
        a geojson dictionary containing a 'coordinates' key, in the desired CRS
        
    """

    geojson_geom = geojson['geometry']
    polygon = ogr.CreateGeometryFromJson(str(geojson_geom))

    source = osr.SpatialReference()
    source.ImportFromEPSG(4326)

    target = osr.SpatialReference()
    target.ImportFromEPSG(EPSG)

    transform = osr.CoordinateTransformation(source, target)
    polygon.Transform(transform)
    
    transformed_geojson = eval(polygon.ExportToJson())

    return transformed_geojson
